"""Opt-in real MySQL/API lifecycle smoke test; temporary data only, no model calls."""
import argparse
import sys
import time
import secrets
from pathlib import Path
import httpx
from sqlalchemy import select
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.db import AccountFileCleanup, Document, Job, User, session_factory
from app.config import settings


def run():
    name = 'lifecycle_' + secrets.token_hex(5)
    password, replacement = secrets.token_urlsafe(20), secrets.token_urlsafe(20)
    def checked(response):
        assert response.is_success, f'API returned {response.status_code}'
        return response.json()
    with httpx.Client(base_url='http://127.0.0.1:8091/api', timeout=15) as client, httpx.Client(base_url='http://127.0.0.1:8091/api', timeout=15) as other:
        account = checked(client.post('/auth/register', json={'username': name, 'password': password}))
        uid, code = account['id'], account['recovery_code']
        try:
            checked(other.post('/auth/login', json={'username': name, 'password': password}))
            checked(client.post('/auth/password/reset', json={'username': name, 'recovery_code': code, 'new_password': replacement}))
            assert other.get('/auth/me').status_code == 401
            assert client.get('/auth/me').status_code == 401
            checked(client.post('/auth/login', json={'username': name, 'password': replacement}))
            checked(client.post('/auth/recovery-code', json={'password': replacement}))
            checked(client.post('/memories', json={'content': 'Temporary lifecycle test'}))
            checked(client.post('/conversations', json={'agent_code': 'contract'}))
            doc = checked(client.post('/rag/documents', files={'file': ('lifecycle-test.txt', b'Temporary lifecycle test only.')}, data={'domain': 'contract'}))
            checked(client.post('/auth/delete-account', json={'password': replacement, 'confirmation': name}))
            assert client.get('/auth/me').status_code == 401
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                with session_factory()() as db:
                    assert db.get(User, uid) is None
                    row = db.get(Document, doc['id']); assert row.deleted and not row.text
                    job = db.scalar(select(Job).where(Job.document_id == row.id))
                    pending_files = list(db.scalars(select(AccountFileCleanup)))
                    pending = any(row.original_path in task.paths for task in pending_files)
                    if job and job.status == 'completed' and not pending and not (settings().storage / row.original_path).exists():
                        print('PASS: register, reset, session revocation, rotation, account closure and worker cleanup (temporary account only).')
                        return
                time.sleep(1)
            raise AssertionError('Background cleanup did not finish within 30 seconds')
        finally:
            # If an earlier assertion failed, leave no live temporary account.
            with session_factory()() as db: exists = db.get(User, uid) is not None
            if exists:
                for candidate in (replacement, password):
                    login = client.post('/auth/login', json={'username': name, 'password': candidate})
                    if login.is_success:
                        checked(client.post('/auth/delete-account', json={'password': candidate, 'confirmation': name})); break

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--execute', action='store_true')
    if not parser.parse_args().execute: parser.error('Use --execute to create and remove a temporary account.')
    run()
