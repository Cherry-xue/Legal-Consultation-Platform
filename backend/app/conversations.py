from typing import Annotated
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from .auth import current_owner
from .db import ChatMessage, Conversation, Run, UserMemory, now, session, session_factory
from .errors import ServiceError
from .profiles import PROFILES

router = APIRouter(prefix='/api', tags=['personal'])
DB = Annotated[Session, Depends(session)]
Owner = Annotated[str, Depends(current_owner)]


def owned_conversation(db, conversation_id, owner, code=None, lock=False):
    stmt = select(Conversation).where(Conversation.id == conversation_id, Conversation.owner == owner)
    item = db.scalar(stmt.with_for_update() if lock else stmt)
    if not item or (code and item.agent_code != code):
        raise ServiceError('CONVERSATION_NOT_FOUND', '会话不存在或不属于当前咨询领域。', 404)
    return item


def conversation_view(item):
    return {k: getattr(item, k) for k in ['id', 'agent_code', 'title', 'created_at', 'updated_at']}


class CreateConversation(BaseModel):
    agent_code: str


@router.post('/conversations')
def create(body: CreateConversation, db: DB, owner: Owner):
    if body.agent_code not in {*PROFILES, 'collaboration'}:
        raise ServiceError('INVALID_AGENT', '请选择有效咨询领域。')
    item = Conversation(owner=owner, agent_code=body.agent_code)
    db.add(item); db.commit()
    return conversation_view(item)


@router.get('/conversations')
def list_conversations(db: DB, owner: Owner, agent_code: str | None = None):
    stmt = select(Conversation).where(Conversation.owner == owner)
    if agent_code:
        stmt = stmt.where(Conversation.agent_code == agent_code)
    return [conversation_view(c) for c in db.scalars(stmt.order_by(Conversation.updated_at.desc()).limit(100))]


@router.get('/conversations/{conversation_id}/messages')
def list_messages(conversation_id: str, db: DB, owner: Owner):
    owned_conversation(db, conversation_id, owner)
    records = db.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
                         .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(100)).all()
    output = []
    for item in reversed(records):
        run = db.get(Run, item.run_id) if item.run_id else None
        output.append({'id': item.id, 'role': item.role, 'text': item.content,
                       'result': run.result if run and run.owner == owner else None})
    return output


@router.delete('/conversations/{conversation_id}')
def remove_conversation(conversation_id: str, db: DB, owner: Owner):
    item = owned_conversation(db, conversation_id, owner, lock=True)
    if item.busy_until and item.busy_until > now():
        raise ServiceError('CONVERSATION_BUSY', '请先停止当前回答，再删除会话。', 409)
    ids = set(db.scalars(select(ChatMessage.run_id).where(ChatMessage.conversation_id == item.id,
                                                          ChatMessage.run_id.is_not(None))))
    db.execute(delete(ChatMessage).where(ChatMessage.conversation_id == item.id))
    for run in db.scalars(select(Run).where(Run.owner == owner)):
        if run.id in ids or run.result.get('parent_run_id') in ids:
            run.query, run.result = '', {'status': 'deleted'}
    db.delete(item); db.commit()
    return {'status': 'deleted'}


class MemoryInput(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    agent_code: str = 'general'


@router.get('/memories')
def memories(db: DB, owner: Owner):
    return [{'id': m.id, 'content': m.content, 'agent_code': m.agent_code} for m in
            db.scalars(select(UserMemory).where(UserMemory.owner == owner).order_by(UserMemory.created_at.desc()).limit(100))]


@router.post('/memories')
def add_memory(body: MemoryInput, db: DB, owner: Owner):
    if body.agent_code not in {*PROFILES, 'general'} or not body.content.strip():
        raise ServiceError('INVALID_MEMORY', '请选择有效范围并填写记忆内容。')
    if len(db.scalars(select(UserMemory.id).where(UserMemory.owner == owner)).all()) >= 50:
        raise ServiceError('MEMORY_LIMIT', '最多保存 50 条记忆，请先清理不需要的内容。')
    row = UserMemory(owner=owner, agent_code=body.agent_code, content=body.content.strip())
    db.add(row); db.commit()
    return {'id': row.id, 'content': row.content, 'agent_code': row.agent_code}


@router.delete('/memories/{memory_id}')
def remove_memory(memory_id: str, db: DB, owner: Owner):
    row = db.scalar(select(UserMemory).where(UserMemory.id == memory_id, UserMemory.owner == owner))
    if not row:
        raise ServiceError('MEMORY_NOT_FOUND', '记忆不存在。', 404)
    db.delete(row); db.commit()
    return {'status': 'deleted'}


def load_context(owner, code, conversation_id=None):
    with session_factory()() as db:
        memory = db.scalars(select(UserMemory).where(UserMemory.owner == owner,
                            UserMemory.agent_code.in_(['general', code])).order_by(UserMemory.created_at.desc()).limit(10)).all()
        context = {'user_notes': [m.content for m in memory], 'history': []}
        if conversation_id:
            owned_conversation(db, conversation_id, owner)
            records = db.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
                                 .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(6)).all()
            for item in reversed(records):
                content = item.content
                if item.run_id:
                    run = db.get(Run, item.run_id)
                    if run and run.owner == owner:
                        content = '\n'.join(c['text'] for c in run.result.get('claims', []))[:1200]
                context['history'].append({'role': item.role, 'content': content[:1500]})
        return context
