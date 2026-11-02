"""Account closure/recovery fixtures never touch real accounts or model providers."""
import asyncio
import hashlib
from datetime import timedelta
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_core import SQLFixture
from app import auth, main, worker
from app.db import (AccountFileCleanup, ChatMessage, Chunk, Conversation, Document, Job,
                    Preview, RecoveryCredential, RetrievalLog, Run, User, UserMemory, UserSession, now)

class RecoveryClosureTests(SQLFixture):
    def register(self):
        client = TestClient(main.app)
        self.addCleanup(client.close)
        response = client.post('/api/auth/register', json={'username': 'recovery_test', 'password': 'test-password-123'})
        self.assertEqual(response.status_code, 200, response.text)
        return client, response.json()

    def test_recovery_is_hashed_one_use_and_revokes_all_sessions(self):
        client, user = self.register()
        code = user['recovery_code']
        with self.factory() as db:
            record = db.get(RecoveryCredential, user['id'])
            self.assertEqual(record.code_hash, hashlib.sha256(code.encode()).hexdigest())
            self.assertNotEqual(record.code_hash, code)
        self.assertNotIn('recovery_code', client.get('/api/auth/me').json())
        other = TestClient(main.app); self.addCleanup(other.close)
        login = other.post('/api/auth/login', json={'username': user['username'], 'password': 'test-password-123'})
        self.assertNotIn('recovery_code', login.json())
        body = {'username': user['username'], 'recovery_code': code, 'new_password': 'new-password-456'}
        self.assertEqual(client.post('/api/auth/password/reset', json=body).status_code, 200)
        self.assertEqual(client.get('/api/auth/me').status_code, 401)
        self.assertEqual(other.get('/api/auth/me').status_code, 401)
        self.assertEqual(client.post('/api/auth/password/reset', json=body).status_code, 400)
        self.assertEqual(client.post('/api/auth/login', json={'username': user['username'], 'password': 'test-password-123'}).status_code, 401)
        self.assertEqual(client.post('/api/auth/login', json={'username': user['username'], 'password': body['new_password']}).status_code, 200)

    def test_rotation_requires_password_and_unknown_accounts_share_error(self):
        client, user = self.register()
        self.assertEqual(client.post('/api/auth/recovery-code', json={'password': 'wrong'}).status_code, 400)
        result = client.post('/api/auth/recovery-code', json={'password': 'test-password-123'})
        self.assertEqual(result.status_code, 200)
        self.assertNotEqual(result.json()['recovery_code'], user['recovery_code'])
        body = {'username': user['username'], 'recovery_code': user['recovery_code'], 'new_password': 'new-password-123'}
        bad = client.post('/api/auth/password/reset', json=body)
        body['username'] = 'missing_account'
        self.assertEqual(client.post('/api/auth/password/reset', json=body).json(), bad.json())
        body['username'] = user['username']; body['recovery_code'] = result.json()['recovery_code']; body['new_password'] = 'short'
        self.assertEqual(client.post('/api/auth/password/reset', json=body).status_code, 422)
        body['new_password'] = 'new-password-123'
        self.assertEqual(client.post('/api/auth/password/reset', json=body).status_code, 200)

    def test_existing_account_can_generate_first_code(self):
        with self.factory() as db:
            user = db.get(User, self.user_id); user.password_hash = auth.hash_password('known-password'); db.commit()
        response = self.client.post('/api/auth/recovery-code', json={'password': 'known-password'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['recovery_code'])

    def test_deletion_requires_confirmation_and_rejects_admin_or_busy_account(self):
        client, user = self.register()
        body = {'password': 'wrong', 'confirmation': user['username']}
        self.assertEqual(client.post('/api/auth/delete-account', json=body).status_code, 400)
        body['password'] = 'test-password-123'; body['confirmation'] = 'other'
        self.assertEqual(client.post('/api/auth/delete-account', json=body).status_code, 400)
        body['confirmation'] = user['username']
        with self.factory() as db:
            record = db.get(User, user['id']); record.is_admin = True; db.commit()
        self.assertEqual(client.post('/api/auth/delete-account', json=body).json()['code'], 'ADMIN_ACCOUNT')
        with self.factory() as db:
            db.get(User, user['id']).is_admin = False
            db.add(Conversation(owner=user['id'], agent_code='contract', busy_until=now()+timedelta(minutes=1))); db.commit()
        self.assertEqual(client.post('/api/auth/delete-account', json=body).json()['code'], 'ACCOUNT_BUSY')
        self.assertEqual(client.get('/api/auth/me').status_code, 200)

    def test_deletion_removes_owned_content_and_queues_private_cleanup_only(self):
        client, user = self.register()
        uploaded = client.post('/api/rag/documents', files={'file': ('private.txt', b'private test text')}, data={'domain': 'contract'}).json()
        doc_id = uploaded['id']
        other_doc = self.upload()
        with self.factory() as db:
            doc = db.get(Document, other_doc); doc.visibility = 'public'
            conv = Conversation(owner=user['id'], agent_code='contract'); db.add(conv); db.flush(); cid = conv.id
            run = Run(owner=user['id'], agent_code='contract', model='test', query='secret', status='completed'); db.add(run); db.flush(); rid = run.id
            db.add_all([ChatMessage(conversation_id=cid, content='private', role='user'), UserMemory(owner=user['id'], content='private note'),
                RetrievalLog(run_id=rid, query='private run', domain='contract', chunk_ids=[], embedding_model='test'),
                RetrievalLog(run_id=user['id'], query='private search', domain='contract', chunk_ids=[], embedding_model='test'),
                RetrievalLog(run_id=self.user_id, query='other', domain='contract', chunk_ids=[], embedding_model='test')])
            preview = Preview(document_id=doc_id, config={}, chunks=[]); db.add(preview); db.flush()
            db.add(Chunk(id='private-chunk', document_id=doc_id, preview_id=preview.id, ordinal=0, text='private', start=0, end=7, embedding_model='test'))
            db.commit()
        body = {'password': 'test-password-123', 'confirmation': user['username']}
        response = client.post('/api/auth/delete-account', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(client.get('/api/auth/me').status_code, 401)
        with self.factory() as db:
            self.assertIsNone(db.get(User, user['id'])); self.assertIsNone(db.get(RecoveryCredential, user['id']))
            self.assertIsNone(db.get(Conversation, cid)); self.assertIsNone(db.get(Run, rid))
            self.assertFalse(list(db.scalars(select(UserMemory).where(UserMemory.owner == user['id']))))
            self.assertFalse(list(db.scalars(select(UserSession).where(UserSession.user_id == user['id']))))
            self.assertEqual(list(db.scalars(select(RetrievalLog.query))), ['other'])
            doc = db.get(Document, doc_id); self.assertTrue(doc.deleted); self.assertEqual(doc.text, '')
            self.assertFalse(db.get(Document, other_doc).deleted)
            job = db.scalar(select(Job).where(Job.document_id == doc_id)); jid = job.id
            self.assertEqual(job.kind, 'delete')
            self.assertIsNotNone(db.scalar(select(AccountFileCleanup)))
        vectors = MagicMock()
        with patch.object(worker, 'collection', return_value=vectors): asyncio.run(worker.process(jid))
        worker.cleanup_account_files()
        with self.factory() as db:
            self.assertIsNone(db.get(Chunk, 'private-chunk'))
            self.assertIsNone(db.scalar(select(Preview).where(Preview.document_id == doc_id)))
            self.assertIsNone(db.scalar(select(AccountFileCleanup)))
        vectors.delete.assert_called_once_with(where={'document_id': doc_id})
        self.assertEqual(self.client.get('/api/auth/me').status_code, 200)

    def test_file_cleanup_retries_without_losing_task(self):
        target = self.config.storage / 'cleanup-test.txt'; target.parent.mkdir(parents=True, exist_ok=True); target.write_text('test')
        with self.factory() as db: db.add(AccountFileCleanup(paths=['cleanup-test.txt'])); db.commit()
        with patch('pathlib.Path.unlink', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError): worker.cleanup_account_files()
        with self.factory() as db: self.assertIsNotNone(db.scalar(select(AccountFileCleanup)))
        worker.cleanup_account_files(); self.assertFalse(target.exists())
