import hashlib
import hmac
import io
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .config import settings
from .db import (User, UserSession, RecoveryCredential, AccountFileCleanup, Document, Job,
                 Conversation, ChatMessage, UserMemory, Run, RetrievalLog, now, session, uid)
from .errors import ServiceError

router = APIRouter(prefix='/api/auth', tags=['account'])
COOKIE = 'legal_session'
DB = Annotated[Session, Depends(session)]


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64 * 1024 * 1024).hex()
    return f'scrypt$32768$8$1${salt}${digest}'


def verify_password(password, stored):
    try:
        kind, n, r, p, salt, expected = stored.split('$')
        if (kind, n, r, p) != ('scrypt', '32768', '8', '1'):
            return False
        actual = hash_password(password, salt).split('$')[-1]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


class Throttle:
    def __init__(self):
        self.entries = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key, limit=20, window=600):
        stamp = time.monotonic()
        with self.lock:
            # Bound bookkeeping; expired IP entries are removed, not live counters.
            if len(self.entries) > 5000:
                for old in list(self.entries):
                    if not self.entries[old] or self.entries[old][-1] < stamp - window:
                        self.entries.pop(old, None)
            queue = self.entries[key]
            while queue and queue[0] < stamp - window:
                queue.popleft()
            if len(queue) >= limit:
                raise ServiceError('TOO_MANY_ATTEMPTS', '尝试过于频繁，请稍后再试。', 429)
            queue.append(stamp)


throttle = Throttle()


def user_view(user):
    return {'id': user.id, 'username': user.username, 'display_name': user.display_name,
            'avatar_url': f'/api/auth/avatar?v={user.avatar_revision}' if user.avatar_revision else None,
            'is_admin': user.is_admin}


def current_user(request: Request, db: DB):
    token = request.cookies.get(COOKIE, '')
    if not token or len(token) > 128:
        raise ServiceError('UNAUTHORIZED', '请先登录后继续。', 401)
    saved = db.get(UserSession, hashlib.sha256(token.encode()).hexdigest())
    if not saved or saved.expires_at <= now():
        raise ServiceError('UNAUTHORIZED', '登录已过期，请重新登录。', 401)
    user = db.get(User, saved.user_id)
    if not user:
        raise ServiceError('UNAUTHORIZED', '账号不可用，请重新登录。', 401)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def current_owner(user: CurrentUser):
    return user.id


def administrator(user: CurrentUser):
    if not user.is_admin:
        raise ServiceError('FORBIDDEN', '此功能仅限后台维护人员。', 403)
    return user


def create_session(db, user, response):
    token = secrets.token_urlsafe(32)
    db.execute(delete(UserSession).where(UserSession.expires_at <= now()))
    db.add(UserSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
                       expires_at=now() + timedelta(days=settings().session_days)))
    db.commit()
    response.set_cookie(COOKIE, token, max_age=settings().session_days * 86400,
                        httponly=True, secure=settings().cookie_secure, samesite='lax', path='/api')


def new_recovery_code(db, user_id):
    code = secrets.token_urlsafe(24)
    db.merge(RecoveryCredential(user_id=user_id, code_hash=hashlib.sha256(code.encode()).hexdigest()))
    return code


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class Registration(Credentials):
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default='', max_length=40)


@router.post('/register')
def register(body: Registration, request: Request, response: Response, db: DB):
    throttle.check(('register', request.client.host if request.client else 'local'), limit=10, window=3600)
    username = body.username.strip().lower()
    if not re.fullmatch(r'[a-z0-9_]{3,32}', username):
        raise ServiceError('INVALID_USERNAME', '账号须为 3–32 位字母、数字或下划线。')
    user = User(username=username, password_hash=hash_password(body.password),
                display_name=body.display_name.strip() or username, is_admin=False)
    try:
        db.add(user)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ServiceError('USERNAME_TAKEN', '该账号已被注册，请更换账号或登录。', 409)
    code = new_recovery_code(db, user.id)
    create_session(db, user, response)
    return {**user_view(user), 'recovery_code': code}


@router.post('/login')
def login(body: Credentials, request: Request, response: Response, db: DB):
    ip = request.client.host if request.client else 'local'
    username = body.username.strip().lower()
    throttle.check(('login-ip', ip), limit=30)
    throttle.check(('login-account', username), limit=12)
    user = db.scalar(select(User).where(User.username == username).with_for_update())
    valid = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if not user or not valid:
        raise ServiceError('INVALID_CREDENTIALS', '账号或密码不正确。', 401)
    old = request.cookies.get(COOKIE)
    if old:
        db.execute(delete(UserSession).where(UserSession.token_hash == hashlib.sha256(old.encode()).hexdigest()))
    create_session(db, user, response)
    return user_view(user)


@router.get('/me')
def me(user: CurrentUser):
    return user_view(user)


@router.post('/logout')
def logout(request: Request, response: Response, db: DB):
    token = request.cookies.get(COOKIE, '')
    if token:
        db.execute(delete(UserSession).where(UserSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        db.commit()
    response.delete_cookie(COOKIE, path='/api', secure=settings().cookie_secure, httponly=True, samesite='lax')
    return {'status': 'logged_out'}


class ProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)


@router.patch('/me')
def update_profile(body: ProfileUpdate, user: CurrentUser, db: DB):
    name = body.display_name.strip()
    if not name:
        raise ServiceError('INVALID_NAME', '昵称不能为空。')
    user.display_name = name
    db.commit()
    return user_view(user)


class PasswordUpdate(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class RecoveryRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


@router.post('/recovery-code')
def generate_recovery(body: RecoveryRequest, user: CurrentUser, db: DB):
    throttle.check(('recovery-generate', user.id), limit=5)
    user = db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    if not user or not verify_password(body.password, user.password_hash):
        raise ServiceError('INVALID_CREDENTIALS', '当前密码不正确。')
    code = new_recovery_code(db, user.id)
    db.commit()
    return {'recovery_code': code}


class PasswordReset(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    recovery_code: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@router.post('/password/reset')
def reset_password(body: PasswordReset, request: Request, response: Response, db: DB):
    username = body.username.strip().lower()
    throttle.check(('reset-ip', request.client.host if request.client else 'local'), limit=15)
    throttle.check(('reset-account', username), limit=5)
    user = db.scalar(select(User).where(User.username == username).with_for_update())
    recovery = db.get(RecoveryCredential, user.id) if user else None
    digest = hashlib.sha256(body.recovery_code.strip().encode()).hexdigest()
    valid = hmac.compare_digest(digest, recovery.code_hash if recovery else '0' * 64)
    if not user or not recovery or not valid:
        raise ServiceError('INVALID_RECOVERY', '账号或找回码不正确。')
    user.password_hash = hash_password(body.new_password)
    db.delete(recovery)  # One-use credential: generate a new code after signing in.
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    response.delete_cookie(COOKIE, path='/api')
    return {'status': 'reset', 'message': '密码已重置，旧登录和找回码已失效，请登录后重新生成找回码。'}


class AccountDeletion(RecoveryRequest):
    confirmation: str = Field(min_length=3, max_length=32)


@router.post('/delete-account')
def delete_account(body: AccountDeletion, user: CurrentUser, response: Response, db: DB):
    throttle.check(('delete-account', user.id), limit=5)
    user = db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    if not user or body.confirmation != user.username or not verify_password(body.password, user.password_hash):
        raise ServiceError('INVALID_CREDENTIALS', '密码或确认账号不正确。')
    if user.is_admin:
        raise ServiceError('ADMIN_ACCOUNT', '管理员账号不支持自助注销，请先处理公共资料及管理员职责。', 409)
    owner = user.id
    docs = list(db.scalars(select(Document).where(Document.owner == owner).with_for_update()))
    doc_ids = [doc.id for doc in docs]
    if db.scalar(select(Job.id).where(Job.document_id.in_(doc_ids), Job.status.in_(['queued', 'running'])).limit(1)) or db.scalar(select(Run.id).where(Run.owner == owner, Run.status == 'running').limit(1)) or db.scalar(select(Conversation.id).where(Conversation.owner == owner, Conversation.busy_until > now()).limit(1)):
        raise ServiceError('ACCOUNT_BUSY', '请等待资料处理和咨询任务结束后再注销。', 409)
    if any(doc.visibility == 'public' for doc in docs):
        raise ServiceError('PUBLIC_DOCUMENTS', '请先移交公共资料，再注销账号。', 409)
    files = [doc.original_path for doc in docs]
    files += [str(path.relative_to(settings().storage)) for path in (settings().storage / 'avatars').glob(f'{owner}_*.jpg')]
    for doc in docs:
        doc.deleted, doc.status, doc.text = True, 'deleted', ''
        doc.name, doc.version, doc.source_url, doc.verified_at, doc.effective_date = '已注销资料', '', '', '', ''
        db.add(Job(document_id=doc.id, kind='delete', idempotency_key='account-delete:' + doc.id))
    if files:
        db.add(AccountFileCleanup(paths=files))
    conversations = select(Conversation.id).where(Conversation.owner == owner)
    run_ids = list(db.scalars(select(Run.id).where(Run.owner == owner)))
    db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversations)))
    db.execute(delete(Conversation).where(Conversation.owner == owner))
    db.execute(delete(RetrievalLog).where(RetrievalLog.run_id.in_(run_ids + [owner])))
    db.execute(delete(Run).where(Run.owner == owner))
    db.execute(delete(UserMemory).where(UserMemory.owner == owner))
    db.execute(delete(RecoveryCredential).where(RecoveryCredential.user_id == owner))
    db.execute(delete(UserSession).where(UserSession.user_id == owner))
    db.delete(user)
    db.commit()
    response.delete_cookie(COOKIE, path='/api')
    return {'status': 'deleted', 'message': '账号已注销，私人资料已退出检索，文件和向量将在后台清理。'}


@router.post('/password')
def update_password(body: PasswordUpdate, user: CurrentUser, response: Response, db: DB):
    user = db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))
    if not verify_password(body.old_password, user.password_hash):
        raise ServiceError('INVALID_CREDENTIALS', '原密码不正确。', 400)
    user.password_hash = hash_password(body.new_password)
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    create_session(db, user, response)
    return {'status': 'updated'}


def encode_avatar(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP'} or image.width * image.height > 16_000_000:
                raise ValueError()
            image.load()
            cropped = ImageOps.fit(ImageOps.exif_transpose(image).convert('RGB'), (256, 256), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            cropped.save(output, 'JPEG', quality=88)
            return output.getvalue()
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise ServiceError('INVALID_IMAGE', '请选择有效的 JPG、PNG 或 WebP 图片（不超过 1600 万像素）。')


@router.post('/avatar')
async def upload_avatar(user: CurrentUser, db: DB, file: UploadFile = File(...)):
    data = await file.read(5 * 1024 * 1024 + 1)
    if not data or len(data) > 5 * 1024 * 1024:
        raise ServiceError('INVALID_IMAGE', '头像文件须小于 5 MB。')
    encoded = await run_in_threadpool(encode_avatar, data)
    folder = settings().storage / 'avatars'
    folder.mkdir(parents=True, exist_ok=True)
    revision = uid()
    path = folder / f'{user.id}_{revision}.jpg'
    path.write_bytes(encoded)
    previous = user.avatar_revision
    try:
        user.avatar_revision = revision
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if previous:
        (folder / f'{user.id}_{previous}.jpg').unlink(missing_ok=True)
    return user_view(user)


@router.get('/avatar')
def avatar(user: CurrentUser):
    path = settings().storage / 'avatars' / f'{user.id}_{user.avatar_revision}.jpg'
    if not user.avatar_revision or not path.is_file():
        raise ServiceError('AVATAR_NOT_FOUND', '尚未设置头像。', 404)
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})
