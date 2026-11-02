import asyncio
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .agents import run_agent
from .collaboration import run_collaboration
from .config import settings
from .auth import administrator, current_owner, router as auth_router
from .conversations import owned_conversation, router as personal_router
from .db import ChatMessage, Chunk, Conversation, Document, Job, Preview, RetrievalLog, Run, User, now, session, session_factory, uid
from .errors import ServiceError
from .profiles import PROFILES, profiles
from .rag import collection, parse_file, retrieve, split_text
from .classification import recommend_domain

app = FastAPI(title='法序 · 法律咨询平台', version='0.2.0')
app.include_router(auth_router)
app.include_router(personal_router)
DOMAIN = Literal['contract', 'labor', 'family', 'consumer', 'procedure', 'general']
SEM = asyncio.Semaphore(4)


@app.middleware('http')
async def browser_protection(request, call_next):
    origin = request.headers.get('origin')
    allowed = {s.strip() for s in settings().allowed_origins.split(',') if s.strip()}
    allowed.add(f'{request.url.scheme}://{request.headers.get("host", "")}')
    if request.method not in {'GET', 'HEAD', 'OPTIONS'} and origin and origin not in allowed:
        return JSONResponse({'code': 'INVALID_ORIGIN', 'message': '请求来源无效，请刷新页面重试。'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


DB = Annotated[Session, Depends(session)]
Owner = Annotated[str, Depends(current_owner)]


@app.exception_handler(ServiceError)
async def service_error(request, error):
    return JSONResponse({'code': error.code, 'message': error.message}, status_code=error.status)


@app.exception_handler(SQLAlchemyError)
async def database_error(request, error):
    return JSONResponse({'code': 'DATABASE_UNAVAILABLE', 'message': '服务暂时不可用，请稍后再试。'}, status_code=503)


@app.get('/api/health')
def health():
    return {'status': 'running', 'version': '0.2.0'}


@app.get('/api/agents')
def list_agents():
    return profiles()


@app.get('/api/ready')
def ready(user: Annotated[User, Depends(administrator)]):
    checks = {}
    try:
        with session_factory()() as db:
            db.execute(text('SELECT 1'))
        checks['mysql'] = True
    except Exception:
        checks['mysql'] = False
    try:
        collection().count()
        checks['chroma'] = True
    except Exception:
        checks['chroma'] = False
    checks['model_key'] = bool(settings().api_key())
    return {'ready': all(checks.values()), 'checks': checks}


def get_doc(db, doc_id, owner, allow_deleted=False, write=False):
    doc = db.get(Document, doc_id)
    if not doc or (doc.deleted and not allow_deleted) or (doc.owner != owner and (write or doc.visibility != 'public')):
        raise ServiceError('DOCUMENT_NOT_FOUND', '资料不存在或无权访问。', 404)
    return doc


def doc_view(doc, owner=None):
    result = {k: getattr(doc, k) for k in ['id', 'name', 'domain', 'visibility', 'source_url', 'version',
                                       'legal_status', 'effective_date', 'verified_at', 'status', 'created_at']}
    if owner is not None:
        result['can_manage'] = doc.owner == owner
    return result


@app.get('/api/rag/documents')
def documents(db: DB, owner: Owner):
    rows = db.scalars(select(Document).where(Document.deleted.is_(False),
                      (Document.owner == owner) | (Document.visibility == 'public')).order_by(Document.created_at.desc()).limit(500))
    return [doc_view(d, owner) for d in rows]


@app.post('/api/rag/recommend-domain')
async def recommend_upload(owner: Owner, file: UploadFile = File(...)):
    name = Path((file.filename or 'document').replace('\\', '/')).name
    if len(name) > 240:
        raise ServiceError('INVALID_METADATA', '文件名过长。')
    data = await file.read(settings().max_upload_mb * 1024 * 1024 + 1)
    if not data or len(data) > settings().max_upload_mb * 1024 * 1024:
        raise ServiceError('INVALID_FILE', f'文件为空或超过 {settings().max_upload_mb} MB。')
    content = await run_in_threadpool(parse_file, name, data)
    # Analysis does not store the upload, create a document, or start indexing.
    return await run_in_threadpool(recommend_domain, name, content)


@app.post('/api/rag/documents')
async def upload(owner: Owner, db: DB, file: UploadFile = File(...), domain: DOMAIN = Form('general'),
                 source_url: str = Form(''), version: str = Form('未核验'),
                 legal_status: Literal['unknown', 'current', 'repealed', 'pending_effective'] = Form('unknown'),
                 effective_date: str = Form(''), verified_at: str = Form(''),
                 visibility: Literal['private', 'public'] = Form('private')):
    if (visibility == 'public' or legal_status != 'unknown') and not db.get(User, owner).is_admin:
        raise ServiceError('FORBIDDEN', '个人上传资料仅供自己的咨询使用，不能发布为公共法律资料。', 403)
    name = Path((file.filename or 'document').replace('\\', '/')).name
    if len(name) > 240 or len(version) > 100 or len(source_url) > 2000 or len(verified_at) > 40:
        raise ServiceError('INVALID_METADATA', '文件名或元数据过长。')
    if source_url and not source_url.startswith(('https://', 'http://')):
        raise ServiceError('INVALID_SOURCE', '来源须为 HTTP/HTTPS 地址。')
    if effective_date:
        from datetime import date
        try:
            date.fromisoformat(effective_date)
        except ValueError:
            raise ServiceError('INVALID_DATE', '日期应为 YYYY-MM-DD。')
    if legal_status != 'unknown' and (not source_url or not verified_at or version == '未核验'):
        raise ServiceError('VERIFICATION_REQUIRED', '登记效力状态前请填写版本、来源与核验人/时间。')
    data = await file.read(settings().max_upload_mb * 1024 * 1024 + 1)
    if not data or len(data) > settings().max_upload_mb * 1024 * 1024:
        raise ServiceError('INVALID_FILE', f'文件为空或超过 {settings().max_upload_mb} MB。')
    content = await run_in_threadpool(parse_file, name, data)
    doc_id = uid()
    relative = f'originals/{doc_id}{Path(name).suffix.lower()}'
    path = settings().storage / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    doc = Document(id=doc_id, owner=owner, name=name, domain=domain, visibility=visibility,
                   source_url=source_url, version=version, legal_status=legal_status,
                   effective_date=effective_date, verified_at=verified_at,
                   content_hash=hashlib.sha256(data).hexdigest(), original_path=relative, text=content)
    try:
        db.add(doc)
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return doc_view(doc)


class DocumentMetadata(BaseModel):
    model_config = {'extra': 'forbid'}
    version: str = Field(min_length=1, max_length=100)
    source_url: str = Field(default='', max_length=2000)
    effective_date: str = Field(default='', max_length=10)
    verified_at: str = Field(default='', max_length=40)
    legal_status: Literal['unknown', 'current', 'repealed', 'pending_effective'] = 'unknown'


@app.patch('/api/rag/documents/{doc_id}/metadata')
def edit_document_metadata(doc_id: str, body: DocumentMetadata, db: DB,
                           user: Annotated[User, Depends(administrator)]):
    from datetime import date
    from urllib.parse import urlsplit
    values = {key: value.strip() for key, value in body.model_dump().items()}
    if not values['version']:
        raise ServiceError('INVALID_METADATA', '版本说明不能为空。')
    if values['source_url']:
        try:
            url = urlsplit(values['source_url'])
        except ValueError:
            raise ServiceError('INVALID_SOURCE', '来源须为 HTTP/HTTPS 地址。')
        if url.scheme not in {'http', 'https'} or not url.netloc:
            raise ServiceError('INVALID_SOURCE', '来源须为 HTTP/HTTPS 地址。')
    if values['effective_date']:
        try:
            date.fromisoformat(values['effective_date'])
        except ValueError:
            raise ServiceError('INVALID_DATE', '日期应为 YYYY-MM-DD。')
    if values['legal_status'] != 'unknown' and (not values['source_url'] or not values['verified_at'] or values['version'] == '未核验'):
        raise ServiceError('VERIFICATION_REQUIRED', '登记效力状态前请填写版本、来源与核验人/时间。')
    doc = db.scalar(select(Document).where(Document.id == doc_id, Document.owner == user.id,
                                          Document.deleted.is_(False)).with_for_update())
    if not doc:
        raise ServiceError('DOCUMENT_NOT_FOUND', '资料不存在或无权修改。', 404)
    for key, value in values.items():
        setattr(doc, key, value)
    db.commit()
    return doc_view(doc, user.id)


class PreviewRequest(BaseModel):
    document_id: str
    max_chars: int = Field(default=800, ge=200, le=2000)
    overlap: int = Field(default=100, ge=0, le=900)


@app.post('/api/rag/chunks/preview')
def preview(body: PreviewRequest, db: DB, owner: Owner):
    doc = get_doc(db, body.document_id, owner, write=True)
    chunks = split_text(doc.text, body.max_chars, body.overlap)
    row = Preview(document_id=doc.id, config=body.model_dump(), chunks=chunks)
    db.add(row)
    db.commit()
    return {'preview_id': row.id, 'count': len(chunks), 'chunks': chunks,
            'expires_at': (row.created_at + timedelta(hours=24)).isoformat()}


class CommitRequest(BaseModel):
    preview_id: str
    idempotency_key: str = Field(min_length=1, max_length=64)


@app.post('/api/rag/chunks/commit')
def commit(body: CommitRequest, db: DB, owner: Owner):
    row = db.get(Preview, body.preview_id)
    if not row:
        raise ServiceError('PREVIEW_NOT_FOUND', '预览不存在。', 404)
    doc = get_doc(db, row.document_id, owner, write=True)
    # Serialize submissions for one document, including different preview versions.
    db.scalar(select(Document).where(Document.id == doc.id).with_for_update())
    key = hashlib.sha256((doc.id + ':' + body.idempotency_key).encode()).hexdigest()
    existing = db.scalar(select(Job).where(Job.idempotency_key == key))
    if existing:
        if existing.preview_id != row.id:
            raise ServiceError('IDEMPOTENCY_CONFLICT', '同一幂等键不能用于不同预览。', 409)
        return {'job_id': existing.id, 'status': existing.status}
    if row.created_at < now() - timedelta(hours=24):
        raise ServiceError('PREVIEW_EXPIRED', '预览已过期，请重新生成。')
    busy = db.scalar(select(Job).where(Job.document_id == doc.id, Job.status.in_(['queued', 'running'])))
    if busy:
        raise ServiceError('DOCUMENT_BUSY', '文档已有处理任务，请等待完成或重试失败任务。', 409)
    job = Job(document_id=doc.id, preview_id=row.id, idempotency_key=key)
    db.add(job)
    doc.status = 'indexing'
    db.commit()
    return {'job_id': job.id, 'status': job.status}


@app.get('/api/rag/jobs')
def jobs(db: DB, owner: Owner):
    rows = db.scalars(select(Job).join(Document).where(Document.owner == owner).order_by(Job.created_at.desc()).limit(100))
    return [{'id': j.id, 'document_id': j.document_id, 'kind': j.kind, 'status': j.status,
             'stage': j.stage, 'attempts': j.attempts, 'error_code': j.error_code} for j in rows]


@app.get('/api/rag/jobs/{job_id}')
def job_status(job_id: str, db: DB, owner: Owner):
    job = db.get(Job, job_id)
    if not job:
        raise ServiceError('JOB_NOT_FOUND', '任务不存在。', 404)
    get_doc(db, job.document_id, owner, allow_deleted=True, write=True)
    return {'id': job.id, 'status': job.status, 'stage': job.stage, 'error_code': job.error_code,
            'attempts': job.attempts}


@app.post('/api/rag/jobs/{job_id}/retry')
def retry_job(job_id: str, db: DB, owner: Owner):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job:
        raise ServiceError('JOB_NOT_FOUND', '任务不存在。', 404)
    get_doc(db, job.document_id, owner, allow_deleted=True, write=True)
    if job.status != 'failed':
        raise ServiceError('JOB_NOT_FAILED', '只能重试失败任务。', 409)
    job.status, job.error_code, job.attempts = 'queued', '', 0
    db.commit()
    return {'job_id': job.id, 'status': job.status}


@app.delete('/api/rag/documents/{doc_id}')
def remove(doc_id: str, db: DB, owner: Owner):
    doc = get_doc(db, doc_id, owner, write=True)
    db.scalar(select(Document).where(Document.id == doc.id).with_for_update())
    doc.deleted, doc.status = True, 'deleted'
    job = Job(document_id=doc.id, kind='delete', idempotency_key='delete:' + doc.id)
    db.add(job)
    # Purge cached source contents from saved answers too, not just vector rows.
    for run in db.scalars(select(Run)):
        if doc.id in json.dumps(run.result):
            run.result = {'status': 'source_deleted', 'notice': '引用来源已删除，历史回答内容已清理。'}
    db.commit()
    return {'job_id': job.id, 'status': 'queued'}


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    domain: DOMAIN = 'general'
    top_k: int = Field(default=5, ge=1, le=10)


@app.post('/api/rag/retrieve')
async def search(body: SearchRequest, db: DB, owner: Owner):
    return {'items': await retrieve(db, body.query, body.domain, body.top_k, owner=owner)}


@app.get('/api/rag/sources/{chunk_id}')
def source(chunk_id: str, db: DB, owner: Owner):
    chunk = db.get(Chunk, chunk_id)
    if not chunk:
        raise ServiceError('SOURCE_NOT_FOUND', '原文不存在或已删除。', 404)
    doc = get_doc(db, chunk.document_id, owner)
    start, end = max(0, chunk.start - 200), min(len(doc.text), chunk.end + 200)
    return {'document': doc_view(doc), 'chunk_id': chunk.id, 'text': doc.text[start:end],
            'highlight_start': chunk.start - start, 'highlight_end': chunk.end - start,
            'original_start': chunk.start, 'original_end': chunk.end}


class ChatRequest(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=32)


def event(name, data):
    return f'event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


@app.post('/api/agents/{code}/chat')
async def chat(code: str, body: ChatRequest, request: Request, owner: Owner, db: DB):
    if code not in {*PROFILES, 'collaboration'}:
        raise ServiceError('INVALID_AGENT', '未知智能体。', 404)
    if body.conversation_id:
        conversation = owned_conversation(db, body.conversation_id, owner, code, lock=True)
        if conversation.busy_until and conversation.busy_until > now():
            raise ServiceError('CONVERSATION_BUSY', '当前会话已有回答生成中，请稍候。', 409)
    else:
        conversation = Conversation(owner=owner, agent_code=code)
        db.add(conversation)
        db.flush()
    conversation_id = conversation.id
    conversation.title = body.query[:80] if conversation.title == '新的咨询' else conversation.title
    conversation.updated_at, conversation.busy_until = now(), now() + timedelta(seconds=120)
    db.add(ChatMessage(conversation_id=conversation_id, role='user', content=body.query))
    db.commit()

    async def stream():
        async def work():
            acquired = False
            try:
                await asyncio.wait_for(SEM.acquire(), timeout=8)
                acquired = True
                call = run_collaboration(body.query, owner, conversation_id=conversation_id) if code == 'collaboration' else run_agent(code, body.query, owner, conversation_id=conversation_id)
                result = await asyncio.wait_for(call, timeout=settings().collaboration_timeout_seconds if code == 'collaboration' else 50)
                result['conversation_id'] = conversation_id
                with session_factory()() as saved:
                    saved.add(ChatMessage(conversation_id=conversation_id, role='assistant', run_id=result.get('run_id')))
                    saved.commit()
                return result
            except BaseException as error:
                with session_factory()() as saved:
                    message = ('本次回答已停止。' if isinstance(error, asyncio.CancelledError) else
                               error.message if isinstance(error, ServiceError) else '本次回答未完成，请稍后重试。')
                    saved.add(ChatMessage(conversation_id=conversation_id, role='assistant', content=message))
                    saved.commit()
                raise
            finally:
                if acquired:
                    SEM.release()
                with session_factory()() as saved:
                    item = saved.get(Conversation, conversation_id)
                    if item and item.owner == owner:
                        item.busy_until, item.updated_at = None, now()
                        saved.commit()
        task = asyncio.create_task(work())
        try:
            yield event('stage', {'stage': 'starting', 'detail': '正在查找相关资料并整理回答…', 'conversation_id': conversation_id})
            while not task.done():
                if await request.is_disconnected():
                    task.cancel()
                    return
                done, _ = await asyncio.wait({task}, timeout=2)
                if not done:
                    yield ': keepalive\n\n'
            result = task.result()
            yield event('result', result)
            yield event('citations', {'items': result.get('citations', [])})
            yield event('done', {'status': result['status'], 'run_id': result.get('run_id')})
        except ServiceError as error:
            yield event('error', {'code': error.code, 'message': error.message})
            yield event('done', {'status': 'failed'})
        except TimeoutError:
            yield event('error', {'code': 'LLM_TIMEOUT', 'message': '本次运行超时，请减少问题范围后重试。'})
            yield event('done', {'status': 'failed'})
        except Exception:
            yield event('error', {'code': 'RUN_FAILED', 'message': '咨询暂未完成，请稍后重试。'})
            yield event('done', {'status': 'failed'})
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except BaseException:
                pass

    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.get('/api/runs/{run_id}')
def run_details(run_id: str, db: DB, owner: Owner):
    run = db.get(Run, run_id)
    if not run or run.owner != owner:
        raise ServiceError('RUN_NOT_FOUND', '运行记录不存在。', 404)
    logs = db.scalars(select(RetrievalLog).where(RetrievalLog.run_id == run.id)).all()
    return {'id': run.id, 'status': run.status, 'agent_code': run.agent_code, 'model': run.model,
            'result': run.result, 'retrieval_logs': [{'id': l.id, 'domain': l.domain,
                                                     'chunk_ids': l.chunk_ids} for l in logs]}
