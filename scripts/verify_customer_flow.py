"""Real HTTP/worker smoke test. Uses only project app credentials and disposable fixtures."""
import argparse
import json
from pathlib import Path
import secrets
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--url', default='http://127.0.0.1:8091')
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute sends real HTTP, embedding and generation requests.')
    from app.db import ChatMessage, Chunk, Conversation, Document, Job, Preview, RetrievalLog, Run, User, UserMemory, UserSession, session_factory
    from app.config import settings
    from sqlalchemy import delete, select
    assert settings().mysql_database == 'legal_assistant'
    client = httpx.Client(base_url=args.url, trust_env=False, timeout=90)
    owner, doc_id = None, None

    def checked(response):
        if response.status_code != 200:
            raise RuntimeError(f'HTTP {response.status_code}: {response.json().get("code", "REQUEST_FAILED")}')
        return response.json()

    def wait_job(job_id):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job = checked(client.get('/api/rag/jobs/' + job_id))
            if job['status'] == 'completed':
                assert job['attempts'] >= 1, 'Expected an actual worker claim.'
                return job
            if job['status'] == 'failed':
                raise RuntimeError('Worker failed: ' + job['error_code'])
            time.sleep(1)
        raise TimeoutError('Worker did not finish within 90 seconds.')

    try:
        username, password = 'http_' + secrets.token_hex(5), secrets.token_urlsafe(24)
        user = checked(client.post('/api/auth/register', json={'username': username, 'password': password}))
        owner = user['id']
        checked(client.post('/api/auth/logout'))
        checked(client.post('/api/auth/login', json={'username': username, 'password': password}))
        checked(client.post('/api/memories', json={'content': '请简洁说明，并提醒演示资料不能作为真实法律使用。'}))
        conv = checked(client.post('/api/conversations', json={'agent_code': 'contract'}))
        fixture = ROOT / 'evals' / 'fixture_演示条文.txt'
        doc = checked(client.post('/api/rag/documents', files={'file': (fixture.name, fixture.read_bytes(), 'text/plain')},
                                  data={'domain': 'general', 'version': 'HTTP disposable fixture'}))
        doc_id = doc['id']
        preview = checked(client.post('/api/rag/chunks/preview', json={'document_id': doc_id, 'max_chars': 300, 'overlap': 40}))
        job = checked(client.post('/api/rag/chunks/commit', json={'preview_id': preview['preview_id'], 'idempotency_key': preview['preview_id']}))
        completed = wait_job(job['job_id'])
        hits = checked(client.post('/api/rag/retrieve', json={'query': '演示资料有法律效力吗？', 'domain': 'contract'}))['items']
        assert hits
        checked(client.get('/api/rag/sources/' + hits[0]['id']))
        response = client.post('/api/agents/contract/chat', json={'query': '根据虚构演示资料，这份资料是否具有法律效力？', 'conversation_id': conv['id']})
        assert response.status_code == 200
        result = None
        for block in response.text.split('\n\n'):
            if block.startswith('event: result\n'):
                result = json.loads(block.split('data: ', 1)[1])
        assert result and result['status'] == 'completed' and result['citations'], 'Grounded HTTP answer failed.'
        history = checked(client.get('/api/conversations/' + conv['id'] + '/messages'))
        assert [m['role'] for m in history] == ['user', 'assistant']
        checked(client.get('/api/runs/' + result['run_id']))
        deletion = checked(client.delete('/api/rag/documents/' + doc_id))
        wait_job(deletion['job_id'])
        assert client.get('/api/rag/sources/' + hits[0]['id']).status_code == 404
        checked(client.delete('/api/conversations/' + conv['id']))
        checked(client.post('/api/auth/logout'))
        assert client.get('/api/auth/me').status_code == 401
        print(json.dumps({'status': 'passed', 'connection': 'project app account only',
                          'registration_login_logout': True, 'persistent_memory': True,
                          'worker_claim_attempts': completed['attempts'], 'chunks': preview['count'],
                          'retrieval_hits': len(hits), 'grounded_answer_citations': len(result['citations']),
                          'restored_history_messages': len(history), 'worker_deletion': True}, indent=2))
    finally:
        if owner:
            with session_factory()() as db:
                # Cleanup only IDs belonging to the disposable user created above.
                doc_ids = list(db.scalars(select(Document.id).where(Document.owner == owner)))
                for document in db.scalars(select(Document).where(Document.owner == owner)):
                    (settings().storage / document.original_path).unlink(missing_ok=True)
                if doc_ids:
                    for entity in [Chunk, Preview, Job]:
                        db.execute(delete(entity).where(entity.document_id.in_(doc_ids)))
                    db.execute(delete(Document).where(Document.owner == owner))
                conv_ids = list(db.scalars(select(Conversation.id).where(Conversation.owner == owner)))
                if conv_ids:
                    db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conv_ids)))
                    db.execute(delete(Conversation).where(Conversation.owner == owner))
                run_ids = list(db.scalars(select(Run.id).where(Run.owner == owner)))
                if run_ids:
                    db.execute(delete(RetrievalLog).where(RetrievalLog.run_id.in_(run_ids)))
                    db.execute(delete(Run).where(Run.owner == owner))
                db.execute(delete(UserMemory).where(UserMemory.owner == owner))
                db.execute(delete(UserSession).where(UserSession.user_id == owner))
                db.execute(delete(User).where(User.id == owner))
                db.commit()
        client.close()
        session_factory().kw['bind'].dispose()
        print('Disposable HTTP test account and records cleaned.')


if __name__ == '__main__':
    main()
