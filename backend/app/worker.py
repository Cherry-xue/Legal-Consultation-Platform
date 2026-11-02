"""Run with python -m app.worker. MySQL queue uses leases and SKIP LOCKED."""
import asyncio
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .db import AccountFileCleanup, Chunk, Document, Job, Preview, init_db, now, session_factory
from .errors import ServiceError
from .provider import embed
from .rag import collection


def claim(db):
    with db.begin():
        for stale in db.scalars(select(Job).where(Job.status == 'running', Job.lease_until < now(),
                                                   Job.attempts >= 3).with_for_update(skip_locked=True)):
            stale.status, stale.error_code = 'failed', 'LEASE_EXHAUSTED'
        job = db.scalar(select(Job).where(
            (Job.status == 'queued') | ((Job.status == 'running') & (Job.lease_until < now())),
            Job.attempts < 3).order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
        if job is None:
            return None
        job.status = 'running'
        job.stage = 'claimed'
        job.attempts += 1
        job.lease_until = now() + timedelta(minutes=10)
        return job.id


async def process(job_id):
    with session_factory()() as db:
        job = db.get(Job, job_id)
        doc = db.get(Document, job.document_id)
        if job.kind == 'delete' or doc.deleted:
            await asyncio.to_thread(collection().delete, where={'document_id': doc.id})
            if doc.deleted:
                settings().storage.joinpath(doc.original_path).unlink(missing_ok=True)
                db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
                db.execute(delete(Preview).where(Preview.document_id == doc.id))
                doc.text = ''
            job.status, job.stage = 'completed', 'deleted'
            db.commit()
            return
        preview = db.get(Preview, job.preview_id)
        if preview is None or preview.document_id != doc.id:
            raise ServiceError('PREVIEW_INVALID', '预览不存在或不属于文档。')
        job.stage = 'embedding'
        db.commit()
        vectors = await embed([c['text'] for c in preview.chunks])
        job.stage = 'writing_vectors'
        db.commit()
        ids = [f'{preview.id}_{i}' for i in range(len(preview.chunks))]
        for start in range(0, len(ids), 100):
            await asyncio.to_thread(collection().upsert, ids=ids[start:start + 100],
                                    embeddings=vectors[start:start + 100],
                                    documents=[c['text'] for c in preview.chunks[start:start + 100]],
                                    metadatas=[{'document_id': doc.id, 'preview_id': preview.id,
                                                'embedding_model': settings().embedding_model_name}
                                               for _ in ids[start:start + 100]])
        # Lock before publication: concurrent deletion must never revive a document.
        db.expire_all()
        doc = db.scalar(select(Document).where(Document.id == doc.id).with_for_update())
        if doc.deleted:
            db.rollback()
            await asyncio.to_thread(collection().delete, where={'document_id': doc.id})
            raise ServiceError('DOCUMENT_DELETED', '文档已删除，取消发布。')
        for i, draft in enumerate(preview.chunks):
            db.merge(Chunk(id=ids[i], document_id=doc.id, preview_id=preview.id,
                           ordinal=i, text=draft['text'], start=draft['start'], end=draft['end'],
                           article=draft['article'], embedding_model=settings().embedding_model_name))
        doc.active_preview_id, doc.status = preview.id, 'indexed'
        job = db.get(Job, job_id)
        job.status, job.stage, job.error_code = 'completed', 'indexed', ''
        db.commit()


def cleanup_account_files():
    with session_factory()() as db:
        row = db.scalar(select(AccountFileCleanup).order_by(AccountFileCleanup.created_at).with_for_update(skip_locked=True).limit(1))
        if row is None:
            return
        root = settings().storage.resolve()
        for relative in row.paths:
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Invalid account cleanup path')
            path.unlink(missing_ok=True)
        db.delete(row); db.commit()


async def run():
    initialized = False
    while True:
        try:
            if not initialized:
                init_db()
                initialized = True
                print('Legal index worker ready. Polling MySQL; no credential values logged.', flush=True)
            with session_factory()() as db:
                job_id = claim(db)
        except SQLAlchemyError:
            # A temporary outage must not permanently stop consumption of the queue.
            print('Worker database unavailable; reconnecting in 5 seconds.', flush=True)
            await asyncio.sleep(5)
            continue
        if not job_id:
            try:
                await asyncio.to_thread(cleanup_account_files)
            except Exception:
                print('Account file cleanup pending; will retry.', flush=True)
            await asyncio.sleep(2)
            continue
        try:
            await asyncio.wait_for(process(job_id), timeout=300)
        except Exception as error:
            try:
                with session_factory()() as db:
                    job = db.get(Job, job_id)
                    job.status, job.error_code = 'failed', error.code if isinstance(error, ServiceError) else 'INDEX_FAILED'
                    db.commit()
            except SQLAlchemyError:
                # The persisted lease makes this job reclaimable after reconnection.
                print('Worker could not save failure; the job will recover through its lease.', flush=True)
                await asyncio.sleep(5)
            print(f'Job {job_id}: failed (details kept out of logs).', flush=True)


if __name__ == '__main__':
    asyncio.run(run())
