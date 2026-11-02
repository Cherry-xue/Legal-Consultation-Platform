"""Explicit real-service test in the legal schema only; never uses smart-home tables.

--admin-env permits a short-lived schema-scoped maintenance connection while a DBA grant
is pending. Admin credentials are never saved, logged, or used by a persistent service.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import secrets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--admin-env')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live explicitly enables embedding and generation requests.')
    if args.admin_env:
        from dotenv import dotenv_values
        source = dotenv_values(args.admin_env, encoding='utf-8-sig')
        for target, origin in [('MYSQL_HOST', 'MYSQL_HOST'), ('MYSQL_PORT', 'MYSQL_PORT'),
                               ('MYSQL_USER', 'MYSQL_USER_ADMIN'), ('MYSQL_PASSWORD', 'MYSQL_PASSWORD_ADMIN')]:
            os.environ[target] = source[origin]
        os.environ['MYSQL_DATABASE'] = 'legal_assistant'
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import settings
    from app.worker import process
    from app.db import session_factory
    from app.agents import run_agent
    from app.rag import collection

    assert settings().mysql_database == 'legal_assistant', 'Only isolated legal schema may be used.'
    client = TestClient(app)
    def checked(response):
        if response.status_code != 200:
            value = response.json()
            raise RuntimeError(f"{response.status_code}: {value.get('code', 'REQUEST_FAILED')}")
        return response.json()
    account = checked(client.post('/api/auth/register', json={'username': 'smoke_' + secrets.token_hex(6),
                        'password': secrets.token_urlsafe(24), 'display_name': '临时链路测试'}))
    owner = account['id']
    fixture = ROOT / 'evals' / 'fixture_演示条文.txt'
    created = checked(client.post('/api/rag/documents', files={'file': (fixture.name, fixture.read_bytes(), 'text/plain')},
                                 data={'domain': 'general', 'version': '虚构测试 v1', 'visibility': 'private'}))
    try:
        preview = checked(client.post('/api/rag/chunks/preview', json={'document_id': created['id'], 'max_chars': 300, 'overlap': 40}))
        job = checked(client.post('/api/rag/chunks/commit', json={'preview_id': preview['preview_id'],
                                                               'idempotency_key': preview['preview_id']}))
        asyncio.run(process(job['job_id']))
        found = checked(client.post('/api/rag/retrieve', json={'query': '测试资料是否具有法律效力？', 'domain': 'contract'}))
        assert found['items'], 'Real vector service returned no hits.'
        source = checked(client.get('/api/rag/sources/' + found['items'][0]['id']))
        assert source['text'], 'Missing original text.'
        results = []
        for code in ['contract', 'labor', 'family', 'consumer', 'procedure']:
            result = asyncio.run(run_agent(code, '请根据演示资料说明：这份资料是否具有法律效力？只引用演示原文。', owner))
            assert result['claims'] and result['citations'], f'{code}: no grounded result'
            results.append({'agent': code, 'status': result['status'], 'claims': len(result['claims']),
                            'citations': len(result['citations']), 'model': result['model']})
    finally:
        # Delete only the fixture created by this explicitly disposable test.
        deletion = checked(client.delete('/api/rag/documents/' + created['id']))
        asyncio.run(process(deletion['job_id']))
        after = collection().get(where={'document_id': created['id']})
        assert not after['ids'], 'Deleted fixture vectors remain.'
    print(json.dumps({'status': 'passed', 'storage': 'real MySQL + Chroma',
                      'maintenance_connection': bool(args.admin_env), 'chunks': preview['count'],
                      'retrieval_hits': len(found['items']), 'agents': results,
                      'disposable_fixture_cleanup': 'passed', 'not_legal_quality_evaluation': True},
                     ensure_ascii=False, indent=2))
    from sqlalchemy import delete
    from app.db import User, UserSession
    with session_factory()() as db:
        db.execute(delete(UserSession).where(UserSession.user_id == owner))
        db.execute(delete(User).where(User.id == owner))
        db.commit()
    client.close()
    session_factory().kw['bind'].dispose()


if __name__ == '__main__':
    main()
