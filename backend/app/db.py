from datetime import datetime, timezone
from functools import lru_cache
from uuid import uuid4
import time

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.dialects.mysql import LONGTEXT

from .config import settings


def uid():
    return uuid4().hex


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def message_uid():
    # MySQL DATETIME may have second precision; sortable IDs break message ties.
    return f'{time.time_ns():020x}{uuid4().hex[:12]}'


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(40))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    avatar_revision: Mapped[str] = mapped_column(String(32), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RecoveryCredential(Base):
    __tablename__ = 'account_recovery'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64))


class AccountFileCleanup(Base):
    __tablename__ = 'account_file_cleanup'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    paths: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserSession(Base):
    __tablename__ = 'user_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    agent_code: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(100), default='新的咨询')
    busy_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=message_uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default='')
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class UserMemory(Base):
    __tablename__ = 'user_memories'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    agent_code: Mapped[str] = mapped_column(String(32), default='general')
    content: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Document(Base):
    __tablename__ = 'documents'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(32), index=True)
    visibility: Mapped[str] = mapped_column(String(16), default='private')
    source_url: Mapped[str] = mapped_column(Text, default='')
    version: Mapped[str] = mapped_column(String(100), default='未核验')
    legal_status: Mapped[str] = mapped_column(String(30), default='unknown')
    effective_date: Mapped[str] = mapped_column(String(10), default='')
    verified_at: Mapped[str] = mapped_column(String(40), default='')
    content_hash: Mapped[str] = mapped_column(String(64))
    original_path: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), 'mysql'))
    status: Mapped[str] = mapped_column(String(32), default='parsed')
    active_preview_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Preview(Base):
    __tablename__ = 'chunk_previews'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'))
    config: Mapped[dict] = mapped_column(JSON)
    chunks: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Chunk(Base):
    __tablename__ = 'chunks'
    id: Mapped[str] = mapped_column(String(70), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'), index=True)
    preview_id: Mapped[str] = mapped_column(String(32), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    article: Mapped[str] = mapped_column(String(80), default='')
    embedding_model: Mapped[str] = mapped_column(String(150))


class Job(Base):
    __tablename__ = 'index_jobs'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey('documents.id'), index=True)
    preview_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default='index')
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    stage: Mapped[str] = mapped_column(String(32), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str] = mapped_column(String(80), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Run(Base):
    __tablename__ = 'agent_runs'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    agent_code: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(150))
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='running')
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RetrievalLog(Base):
    __tablename__ = 'retrieval_logs'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    query: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(32))
    chunk_ids: Mapped[list] = mapped_column(JSON)
    embedding_model: Mapped[str] = mapped_column(String(150))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


@lru_cache
def session_factory():
    engine = create_engine(settings().db_url, pool_pre_ping=True, connect_args={'connect_timeout': 3})
    return sessionmaker(engine, expire_on_commit=False)


def init_db():
    factory = session_factory()
    Base.metadata.create_all(factory.kw['bind'])


def session():
    with session_factory()() as db:
        yield db
