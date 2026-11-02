import asyncio
import hashlib
import io
import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from PIL import Image
from sqlalchemy import select
from fastapi.testclient import TestClient

from test_core import SQLFixture, TOKEN
from app import agents, auth, conversations, main
from app.db import ChatMessage, Conversation, Document, Run, User, UserMemory, UserSession, now


class AccountTests(SQLFixture):
    """Reuses SQL fixture; additional assertions exercise authentication and two users."""
    def register(self, username='bob', password='test-password-123'):
        client = TestClient(main.app)
        response = client.post('/api/auth/register', json={'username': username, 'password': password,
                                                          'display_name': username.title(), 'is_admin': True})
        self.assertEqual(response.status_code, 200, response.text)
        self.addCleanup(client.close)
        return client, response.json()

    def test_register_hashes_password_and_sets_private_cookie(self):
        client, user = self.register()
        self.assertFalse(user['is_admin'])
        self.assertNotIn('password_hash', user)
        self.assertEqual(client.get('/api/auth/me').json()['id'], user['id'])
        with self.factory() as db:
            record = db.get(User, user['id'])
            self.assertNotEqual(record.password_hash, 'test-password-123')
            self.assertTrue(auth.verify_password('test-password-123', record.password_hash))
            raw = client.cookies.get(auth.COOKIE)
            self.assertIsNotNone(db.get(UserSession, hashlib.sha256(raw.encode()).hexdigest()))

    def test_login_logout_duplicate_and_invalid_credentials(self):
        client, user = self.register()
        self.assertEqual(client.post('/api/auth/register', json={'username': 'BOB', 'password': 'abcdefgh'}).status_code, 409)
        old_cookie = client.cookies.get(auth.COOKIE)
        self.assertEqual(client.post('/api/auth/logout').status_code, 200)
        self.assertEqual(client.get('/api/auth/me').status_code, 401)
        with self.factory() as db:
            self.assertIsNone(db.get(UserSession, hashlib.sha256(old_cookie.encode()).hexdigest()))
        self.assertEqual(client.post('/api/auth/login', json={'username': 'bob', 'password': 'incorrect-pass'}).status_code, 401)
        response = client.post('/api/auth/login', json={'username': 'BOB', 'password': 'test-password-123'})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers['set-cookie'].lower()
        self.assertIn('httponly', cookie)
        self.assertIn('samesite=lax', cookie)
        self.assertNotIn('password', response.text)

    def test_expired_session_and_legacy_bearer_denied(self):
        with self.factory() as db:
            row = db.get(UserSession, hashlib.sha256(TOKEN.encode()).hexdigest())
            row.expires_at = now() - timedelta(seconds=1); db.commit()
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)
        self.client.cookies.clear()
        self.client.headers['Authorization'] = 'Bearer ' + TOKEN
        self.assertEqual(self.client.get('/api/rag/documents').status_code, 401)

    def test_password_change_revokes_other_sessions(self):
        client, user = self.register()
        other = TestClient(main.app)
        self.addCleanup(other.close)
        other.post('/api/auth/login', json={'username': 'bob', 'password': 'test-password-123'})
        self.assertEqual(client.post('/api/auth/password', json={'old_password': 'wrong', 'new_password': 'new-password-123'}).status_code, 400)
        result = client.post('/api/auth/password', json={'old_password': 'test-password-123', 'new_password': 'new-password-123'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(client.get('/api/auth/me').status_code, 200)
        self.assertEqual(other.get('/api/auth/me').status_code, 401)

    def test_csrf_admin_privilege_and_public_document_mutation(self):
        self.assertEqual(self.client.post('/api/auth/logout', headers={'Origin': 'https://malicious.example'}).status_code, 403)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 200)
        self.assertEqual(self.client.get('/api/ready').status_code, 403)
        self.assertEqual(self.client.post('/api/admin/init').status_code, 404)
        doc = self.upload()
        with self.factory() as db:
            row = db.get(Document, doc); row.owner = 'someone-else'; row.visibility = 'public'; db.commit()
        self.assertEqual(self.client.delete('/api/rag/documents/' + doc).status_code, 404)
        self.assertEqual(self.client.post('/api/rag/chunks/preview', json={'document_id': doc}).status_code, 404)

    def test_memories_conversations_documents_and_runs_are_user_scoped(self):
        bob, user = self.register()
        memory = self.client.post('/api/memories', json={'content': 'Alice private note'}).json()
        conversation = self.client.post('/api/conversations', json={'agent_code': 'contract'}).json()
        doc = self.upload()
        with self.factory() as db:
            run = Run(owner=self.user_id, agent_code='contract', model='test', query='Alice question', result={'claims': []})
            db.add(run); db.commit(); run_id = run.id
        self.assertEqual(bob.get('/api/memories').json(), [])
        self.assertEqual(bob.get('/api/conversations').json(), [])
        self.assertEqual(bob.get('/api/rag/documents').json(), [])
        self.assertEqual(bob.delete('/api/memories/' + memory['id']).status_code, 404)
        self.assertEqual(bob.get('/api/conversations/' + conversation['id'] + '/messages').status_code, 404)
        self.assertEqual(bob.delete('/api/conversations/' + conversation['id']).status_code, 404)
        self.assertEqual(bob.get('/api/runs/' + run_id).status_code, 404)
        context = conversations.load_context(user['id'], 'contract')
        self.assertEqual(context['user_notes'], [])
        self.assertEqual(conversations.load_context(self.user_id, 'contract')['user_notes'], ['Alice private note'])
        self.assertEqual(bob.delete('/api/rag/documents/' + doc).status_code, 404)

    def test_avatar_validation_reencoding_and_identity(self):
        image = io.BytesIO()
        Image.new('RGB', (600, 300), '#357251').save(image, 'PNG')
        response = self.client.post('/api/auth/avatar', files={'file': ('avatar.png', image.getvalue(), 'image/png')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['avatar_url'].startswith('/api/auth/avatar?v='))
        downloaded = self.client.get(response.json()['avatar_url'])
        self.assertEqual(downloaded.headers['content-type'], 'image/jpeg')
        self.assertEqual(Image.open(io.BytesIO(downloaded.content)).size, (256, 256))
        self.assertIn('no-store', downloaded.headers['cache-control'])
        bob, _ = self.register()
        self.assertEqual(bob.get('/api/auth/avatar').status_code, 404)
        self.assertEqual(self.client.post('/api/auth/avatar', files={'file': ('fake.png', b'not an image')}).status_code, 400)

    def test_model_context_contains_only_own_relevant_memories(self):
        bob, user = self.register()
        self.client.post('/api/memories', json={'content': 'Alice all-domain preference'})
        self.client.post('/api/memories', json={'content': 'Alice contract preference', 'agent_code': 'contract'})
        self.client.post('/api/memories', json={'content': 'Alice labor-only preference', 'agent_code': 'labor'})
        bob.post('/api/memories', json={'content': 'Bob private preference'})
        doc_id = self.upload()
        hits = [{'id': 'source', 'document_id': doc_id, 'text': '这是一段完整的测试原文，仅用于技术测试。', 'article': ''}]
        output = {'claims': [{'text': '测试说明', 'evidence_id': 'source', 'quote': '这是一段完整的测试原文'}]}
        with patch.object(agents, 'retrieve', new=AsyncMock(return_value=hits)), \
             patch.object(agents, 'complete', new=AsyncMock(return_value=(output, {}))) as model:
            asyncio.run(agents.run_agent('contract', '测试问题', self.user_id))
        context = json.loads(model.call_args.args[2])['user_context']
        self.assertCountEqual(context['user_notes'], ['Alice all-domain preference', 'Alice contract preference'])

    def test_chat_persists_owned_history_and_rejects_cross_user_conversation(self):
        conv = self.client.post('/api/conversations', json={'agent_code': 'contract'}).json()
        async def stub(code, query, owner, **kwargs):
            with self.factory() as db:
                run = Run(owner=owner, agent_code=code, model='test', query=query, result={'claims': [{'text': 'answer'}]})
                db.add(run); db.commit(); run_id = run.id
            return {'run_id': run_id, 'status': 'completed', 'claims': [{'text': 'answer'}], 'citations': []}
        with patch.object(main, 'run_agent', side_effect=stub):
            response = self.client.post('/api/agents/contract/chat', json={'query': '我的问题', 'conversation_id': conv['id']})
        self.assertIn('event: result', response.text)
        messages = self.client.get('/api/conversations/' + conv['id'] + '/messages').json()
        self.assertEqual([m['role'] for m in messages], ['user', 'assistant'])
        self.assertEqual(messages[1]['result']['claims'][0]['text'], 'answer')
        bob, _ = self.register()
        self.assertEqual(bob.post('/api/agents/contract/chat', json={'query': '继续', 'conversation_id': conv['id']}).status_code, 404)
        self.assertEqual(self.client.post('/api/agents/labor/chat', json={'query': '继续', 'conversation_id': conv['id']}).status_code, 404)

    def test_conversation_delete_clears_history_and_run_content(self):
        conv = self.client.post('/api/conversations', json={'agent_code': 'contract'}).json()
        with self.factory() as db:
            run = Run(owner=self.user_id, agent_code='contract', model='test', query='private', result={'claims': []})
            db.add(run); db.flush(); rid = run.id
            db.add(ChatMessage(conversation_id=conv['id'], role='assistant', run_id=rid)); db.commit()
        self.assertEqual(self.client.delete('/api/conversations/' + conv['id']).status_code, 200)
        with self.factory() as db:
            self.assertEqual(db.get(Run, rid).query, '')
            self.assertEqual(db.get(Run, rid).result, {'status': 'deleted'})
