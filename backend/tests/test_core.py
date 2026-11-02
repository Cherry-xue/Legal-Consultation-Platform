"""Offline verification uses SQLite as a test double only; application storage remains MySQL."""
import asyncio
import json
import sys
import tempfile
import unittest
import hashlib
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import agents, auth, collaboration, conversations, main, rag, worker
from app.config import Settings
from app.db import Base, ChatMessage, Chunk, Conversation, Document, Job, Preview, Run, RetrievalLog, User, UserSession, UserMemory, now
from app.errors import ServiceError

TOKEN = 'test-only-token-not-for-production-123456'


class SQLFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Settings(_env_file=None, data_dir=self.tmp.name)
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        self.engine = engine
        Base.metadata.create_all(engine)
        self.factory = sessionmaker(engine, expire_on_commit=False)
        def dependency():
            with self.factory() as db:
                yield db
        main.app.dependency_overrides[main.session] = dependency
        self.patches = [patch.object(module, 'settings', return_value=self.config)
                        for module in (main, rag, worker, auth)]
        self.patches += [patch.object(module, 'session_factory', return_value=self.factory)
                         for module in (main, agents, collaboration, worker, conversations)]
        for p in self.patches:
            p.start()
        auth.throttle.entries.clear()
        self.user_id = 'a' * 32
        with self.factory() as db:
            db.add(User(id=self.user_id, username='alice', display_name='Alice', password_hash=auth.DUMMY_HASH))
            db.flush()
            db.add(UserSession(token_hash=hashlib.sha256(TOKEN.encode()).hexdigest(), user_id=self.user_id,
                               expires_at=now() + timedelta(days=1)))
            db.commit()
        self.client = TestClient(main.app)
        self.client.cookies.set(auth.COOKIE, TOKEN, path='/api')

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()
        for p in reversed(self.patches):
            p.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def upload(self, content='第一条 本测试资料仅用于演示检索，不是真实法律。\n第二条 测试引用应当对应原文。', **fields):
        response = self.client.post('/api/rag/documents', files={'file': ('sample.txt', content.encode(), 'text/plain')},
                                    data={'domain': 'contract', **fields})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def preview(self, doc):
        response = self.client.post('/api/rag/chunks/preview', json={'document_id': doc})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['preview_id']

class CoreTests(SQLFixture):
    def test_auth_protects_private_routes(self):
        self.assertEqual(self.client.get('/api/agents').status_code, 200)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/rag/documents').status_code, 401)
        self.assertEqual(self.client.get('/api/ready').status_code, 401)

    def test_credentials_are_not_in_health(self):
        value = self.client.get('/api/health').text
        self.assertNotIn(TOKEN, value)
        self.assertNotIn('api_key', value)

    def test_upload_rejects_empty_unsupported_and_false_verification(self):
        self.assertEqual(self.client.post('/api/rag/documents', files={'file': ('x.txt', b'')}).status_code, 400)
        self.assertEqual(self.client.post('/api/rag/documents', files={'file': ('x.exe', b'hello')}).status_code, 400)
        res = self.client.post('/api/rag/documents', files={'file': ('x.txt', b'hello')}, data={'legal_status': 'current'})
        self.assertEqual(res.json()['code'], 'FORBIDDEN')

    def test_preview_exact_offsets_and_no_item_as_paragraph(self):
        content = '序言\n第一条 这是一条测试条文。\n（一）这一段是项。\n（二）这一段也是项。\n第二条 后续正文。'
        chunks = rag.split_text(content, 200, 20)
        self.assertEqual(len(chunks), 3)
        self.assertIn('（一）', chunks[1]['text'])
        self.assertEqual(chunks[1]['article'], '第一条')
        for c in chunks:
            self.assertEqual(c['text'], content[c['start']:c['end']])
            self.assertNotIn('第1款', c['text'])

    def test_long_text_bounded_and_covered(self):
        content = '第一条 ' + '测试正文' * 800
        chunks = rag.split_text(content, 200, 20)
        self.assertTrue(all(len(c['text']) <= 200 for c in chunks))
        self.assertEqual(chunks[0]['start'], 0)
        self.assertEqual(chunks[-1]['end'], len(content))
        self.assertTrue(all(a['end'] >= b['start'] for a, b in zip(chunks, chunks[1:])))
        with self.assertRaises(ServiceError):
            rag.split_text(content, 200, 100)

    def test_markdown_article_headings_keep_offsets_and_subchunk_identity(self):
        content = '# 测试合同\n\n## 第六条 单方解除\n' + '测试解除条件。' * 80 + '\n### 第七条 争议材料\n保存凭证。'
        chunks = rag.split_text(content, 200, 20)
        sixth = [c for c in chunks if c['article'] == '第六条']
        self.assertGreater(len(sixth), 1)
        self.assertEqual(chunks[-1]['article'], '第七条')
        self.assertEqual(chunks[0]['article'], '')
        self.assertEqual(chunks[0]['start'], 0)
        self.assertEqual(chunks[-1]['end'], len(content))
        for c in chunks:
            self.assertEqual(c['text'], content[c['start']:c['end']])
        self.assertTrue(all(a['end'] >= b['start'] for a, b in zip(chunks, chunks[1:])))

    def test_article_validation_rejects_cross_chunk_and_subarticle_mismatch(self):
        hits = [{'id': 'seventh', 'text': '第七条 保存合同版本。', 'article': '第七条'},
                {'id': 'sixth', 'text': '仅为测试的解除条件。', 'article': '第六条'},
                {'id': 'sub', 'text': '第六条之一 保存原始凭证。', 'article': '第六条之一'}]
        for text, source, quote in [('依据第六条说明', 'seventh', '保存合同版本'),
                                    ('依据第五百六十二条说明', 'sixth', '仅为测试的解除条件'),
                                    ('依据第六条之二说明', 'sub', '保存原始凭证')]:
            with self.assertRaises(ServiceError):
                agents.validate_answer({'claims': [{'text': text, 'evidence_id': source, 'quote': quote}]}, hits)
        valid = {'claims': [{'text': '第六条约定解除条件', 'evidence_id': 'sixth', 'quote': '仅为测试的解除条件'}]}
        self.assertEqual(len(agents.validate_answer(valid, hits).claims), 1)

    def test_commit_idempotency_and_conflict(self):
        doc = self.upload()
        preview = self.preview(doc)
        payload = {'preview_id': preview, 'idempotency_key': 'same'}
        first = self.client.post('/api/rag/chunks/commit', json=payload)
        second = self.client.post('/api/rag/chunks/commit', json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()['job_id'], second.json()['job_id'])
        other = self.preview(doc)
        res = self.client.post('/api/rag/chunks/commit', json={'preview_id': other, 'idempotency_key': 'same'})
        self.assertEqual(res.status_code, 409)

    def test_foreign_private_document_denied(self):
        doc = self.upload()
        with self.factory() as db:
            db.get(Document, doc).owner = 'other-user'
            db.commit()
        self.assertEqual(self.client.post('/api/rag/chunks/preview', json={'document_id': doc}).status_code, 404)
        self.assertEqual(self.client.delete('/api/rag/documents/' + doc).status_code, 404)

    def test_delete_hides_source_and_scrubs_saved_answers(self):
        doc = self.upload()
        with self.factory() as db:
            run = Run(owner=self.user_id, agent_code='contract', model='test', query='test', result={'citations': [{'document_id': doc, 'text': 'private'}]})
            db.add(run); db.commit(); rid = run.id
        response = self.client.delete('/api/rag/documents/' + doc)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/rag/documents').json(), [])
        with self.factory() as db:
            self.assertEqual(db.get(Run, rid).result['status'], 'source_deleted')

    def test_worker_real_sql_publication_and_retrieval_log(self):
        doc_id = self.upload()
        preview = self.preview(doc_id)
        response = self.client.post('/api/rag/chunks/commit', json={'preview_id': preview, 'idempotency_key': preview})
        job_id = response.json()['job_id']
        fake = MagicMock()
        async def embeddings(texts):
            return [[0.1, 0.2, 0.3] for _ in texts]
        with patch.object(worker, 'collection', return_value=fake), patch.object(worker, 'embed', side_effect=embeddings):
            asyncio.run(worker.process(job_id))
        with self.factory() as db:
            self.assertEqual(db.get(Job, job_id).status, 'completed')
            self.assertEqual(db.get(Document, doc_id).active_preview_id, preview)
            chunk = db.scalar(select(Chunk).where(Chunk.document_id == doc_id))
            cid = chunk.id
        fake.query.return_value = {'ids': [[cid, 'nonexistent']], 'distances': [[0.1, 0.2]]}
        with patch.object(rag, 'collection', return_value=fake), patch.object(rag, 'embed', side_effect=embeddings):
            result = self.client.post('/api/rag/retrieve', json={'query': '测试引用', 'domain': 'contract'})
        self.assertEqual(result.status_code, 200, result.text)
        returned = [row['id'] for row in result.json()['items']]
        self.assertIn(cid, returned)
        self.assertNotIn('nonexistent', returned)
        self.assertEqual(self.client.get('/api/rag/sources/' + cid).status_code, 200)
        with self.factory() as db:
            self.assertEqual(len(db.scalars(select(RetrievalLog)).all()), 1)

    def test_citation_validation_rejects_unknown_id_or_fabricated_quote(self):
        hits = [{'id': 'known', 'text': '这是一段真实的测试引用原文，不具有法律效力。', 'article': ''}]
        valid = {'claims': [{'text': '测试资料的说明', 'evidence_id': 'known', 'quote': '这是一段真实的测试引用原文'}]}
        self.assertEqual(len(agents.validate_answer(valid, hits).claims), 1)
        for claim in [{'text': '伪造', 'evidence_id': 'unknown', 'quote': '这是一段真实的测试引用原文'},
                      {'text': '伪造', 'evidence_id': 'known', 'quote': '根本不存在的原文摘录'}]:
            with self.assertRaises(ServiceError):
                agents.validate_answer({'claims': [claim]}, hits)

    def test_collaboration_plan_rejects_recursion_duplicate_and_arbitrary_tools(self):
        for codes in [('collaboration', 'contract'), ('contract', 'contract'), ('shell', 'consumer')]:
            with self.assertRaises(ServiceError):
                collaboration.validate_plan({'tasks': [{'agent_code': c, 'question': '测试问题'} for c in codes]})
        tasks = collaboration.validate_plan({'tasks': [{'agent_code': c, 'question': '测试问题'} for c in ['consumer', 'procedure']]})
        self.assertEqual(len(tasks), 2)

    def test_sse_failure_has_terminal_event(self):
        with patch.object(main, 'run_agent', new=AsyncMock(side_effect=ServiceError('NO_BASIS', '证据不足'))):
            response = self.client.post('/api/agents/contract/chat', json={'query': '测试提问'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('event: error', response.text)
        self.assertIn('event: done', response.text)
        self.assertIn('NO_BASIS', response.text)

    def test_agent_repairs_quote_once_without_weakening_validation(self):
        doc_id = self.upload()
        hits = [{'id': 'known', 'document_id': doc_id, 'text': '这是一段真实的测试引用原文，不具有法律效力。', 'article': ''}]
        invalid = {'claims': [{'text': '测试说明', 'evidence_id': 'wrong', 'quote': '这是一段真实的测试引用原文'}]}
        valid = {'claims': [{'text': '测试说明', 'evidence_id': 'known', 'quote': '这是一段真实的测试引用原文'}]}
        with patch.object(agents, 'retrieve', new=AsyncMock(return_value=hits)), \
             patch.object(agents, 'complete', new=AsyncMock(side_effect=[(invalid, {}), (valid, {})])) as model:
            result = asyncio.run(agents.run_agent('contract', '测试问题', self.user_id))
        self.assertEqual(model.await_count, 2)
        repair = json.loads(model.await_args_list[1].args[2])
        self.assertEqual(repair['validation_error']['code'], 'CITATION_INVALID')
        self.assertIn('不得补充证据外的法条', repair['correction'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['claims'][0]['evidence_id'], 'known')

    def test_empty_filtered_retrieval_returns_no_basis_without_generation(self):
        with patch.object(agents, 'retrieve', AsyncMock(return_value=[])), \
                patch.object(agents, 'complete', AsyncMock()) as model:
            result = asyncio.run(agents.run_agent('contract', '资料外问题', self.user_id))
        self.assertEqual(result['status'], 'no_basis')
        self.assertEqual(result['claims'], [])
        self.assertEqual(result['usage_by_attempt'], [])
        model.assert_not_awaited()
        with self.factory() as db:
            self.assertEqual(db.get(Run, result['run_id']).status, 'no_basis')

    def test_after_repair_only_individually_valid_claims_are_published(self):
        doc_id = self.upload()
        hits = [{'id': 'known', 'document_id': doc_id, 'text': '这是一段真实的测试引用原文，不具有法律效力。', 'article': ''}]
        good = {'text': '测试资料说明', 'evidence_id': 'known', 'quote': '这是一段真实的测试引用原文'}
        bad = {'text': '未经支持的结论', 'evidence_id': 'invented', 'quote': '根本不存在的原文'}
        for claims, status, expected in [([good, bad], 'partial', [good]), ([bad], 'no_basis', [])]:
            raw = {'claims': claims}
            with patch.object(agents, 'retrieve', new=AsyncMock(return_value=hits)), \
                    patch.object(agents, 'complete', new=AsyncMock(return_value=(raw, {}))) as model:
                result = asyncio.run(agents.run_agent('contract', '测试边界', self.user_id))
            self.assertEqual(model.await_count, 2)
            self.assertEqual(result['status'], status)
            self.assertEqual(result['claims'], expected)
            self.assertEqual(result['omitted_claims'], 1)
            self.assertTrue(result['notice'])
            self.assertNotIn('invented', [hit['id'] for hit in result['citations']])

    def test_collaboration_partial_preserves_success_and_citations(self):
        async def one(code, *args):
            if code == 'procedure':
                raise ServiceError('LLM_TIMEOUT', 'timeout')
            return {'status': 'completed', 'claims': [], 'citations': [{'id': 'source'}]}
        state = {'tasks': [{'agent_code': c, 'question': 'test'} for c in ['consumer', 'procedure']],
                 'query': 'original', 'owner': self.user_id, 'run_id': 'run'}
        with patch.object(collaboration, 'run_agent', side_effect=one):
            state.update(asyncio.run(collaboration.dispatch(state)))
        value = asyncio.run(collaboration.aggregate(state))['result']
        self.assertEqual(value['status'], 'partial')
        self.assertEqual(value['citations'], [{'id': 'source'}])


if __name__ == '__main__':
    unittest.main()
