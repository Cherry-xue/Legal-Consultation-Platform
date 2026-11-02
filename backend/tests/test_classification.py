import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from test_core import SQLFixture
from app.classification import recommend_domain
from app import rag
from app.db import Chunk, Document, Job, User


class ClassificationTests(unittest.TestCase):
    def test_statutes_and_specific_excerpts(self):
        cases = [('中华人民共和国民法典.md', '第一条 测试文本。', 'general'),
                 ('劳动合同法.txt', '劳动合同与用人单位。', 'labor'),
                 ('民法典合同编节选.md', '合同编的测试资料。', 'contract'),
                 ('民事诉讼法.txt', '起诉、管辖和举证。', 'procedure'),
                 ('待归类.txt', '没有明显领域特征的资料。', 'general')]
        for name, text, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(recommend_domain(name, text)['domain'], expected)
        self.assertEqual(recommend_domain('待归类.txt', '未知内容')['confidence'], 'low')

    def test_authored_fixtures_route_to_their_domains(self):
        folder = Path(__file__).resolve().parents[2] / 'docs' / '测试材料'
        expected = ['contract', 'labor', 'family', 'consumer', 'procedure', 'contract']
        files = sorted(path for path in folder.iterdir() if path.name[0].isdigit())
        self.assertEqual(len(files), 6)
        for path, domain in zip(files, expected):
            with self.subTest(file=path.name):
                self.assertEqual(recommend_domain(path.name, path.read_text(encoding='utf-8'))['domain'], domain)

    def test_balanced_multiple_domains_suggest_general_and_citations_do_not_override_title(self):
        self.assertEqual(recommend_domain('综合事项.txt', '工资、加班、考勤。抚养、离婚、继承。')['domain'], 'general')
        self.assertEqual(recommend_domain('采购合同.md', '采购合同\n版本一\n业务范围：合同审查\n根据民法典讨论验收、质量保证金。')['domain'], 'contract')


class ClassificationAPITests(SQLFixture):
    def test_metadata_edit_permissions_validation_and_index_preservation(self):
        doc_id = self.upload()
        url = f'/api/rag/documents/{doc_id}/metadata'
        data = dict(version='2023年修正版', source_url='https://example.org/law',
                    effective_date='2024-01-01', verified_at='测试管理员，2026-10-28', legal_status='current')
        self.assertEqual(self.client.patch(url, json=data).status_code, 403)
        with self.factory() as db:
            db.get(User, self.user_id).is_admin = True
            doc = db.get(Document, doc_id)
            doc.active_preview_id, doc.status = 'original-index', 'indexed'
            original = (doc.content_hash, doc.text, doc.owner, doc.visibility)
            db.commit()
        self.assertEqual(self.client.patch(url, json={**data, 'verified_at': ''}).status_code, 400)
        self.assertEqual(self.client.patch(url, json={**data, 'effective_date': '2024-02-30'}).status_code, 400)
        self.assertEqual(self.client.patch(url, json={**data, 'source_url': 'javascript:alert(1)'}).status_code, 400)
        self.assertEqual(self.client.patch(url, json={**data, 'visibility': 'public'}).status_code, 422)
        result = self.client.patch(url, json=data)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['version'], data['version'])
        with self.factory() as db:
            doc = db.get(Document, doc_id)
            self.assertEqual((doc.content_hash, doc.text, doc.owner, doc.visibility), original)
            self.assertEqual(doc.active_preview_id, 'original-index')
            self.assertEqual(doc.status, 'indexed')
            self.assertEqual(list(db.scalars(select(Job))), [])
            doc.owner, doc.visibility = 'another-admin', 'public'
            db.commit()
        self.assertEqual(self.client.patch(url, json=data).status_code, 404)

    def test_only_admin_can_upload_public_general_documents(self):
        payload = {'file': ('public.txt', '第一条 公共通用测试法规。'.encode())}
        denied = self.client.post('/api/rag/documents', files=payload, data={'domain': 'general', 'visibility': 'public'})
        self.assertEqual(denied.status_code, 403)
        with self.factory() as db:
            db.get(User, self.user_id).is_admin = True
            db.commit()
        created = self.client.post('/api/rag/documents', files=payload, data={'domain': 'general', 'visibility': 'public'})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()['visibility'], 'public')
        self.assertEqual(created.json()['domain'], 'general')
        rows = self.client.get('/api/rag/documents').json()
        self.assertTrue(rows[0]['can_manage'])

    def test_analysis_is_authenticated_and_does_not_create_documents_or_jobs(self):
        response = self.client.post('/api/rag/recommend-domain', files={'file': ('劳动合同.txt', '工资及加班安排。'.encode())})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['domain'], 'labor')
        with self.factory() as db:
            self.assertEqual(list(db.scalars(select(Document))), [])
            self.assertEqual(list(db.scalars(select(Job))), [])
        self.assertEqual(list(Path(self.tmp.name).rglob('*')), [])
        self.assertEqual(self.client.post('/api/rag/recommend-domain', files={'file': ('empty.txt', b'')}).status_code, 400)
        self.assertEqual(self.client.post('/api/rag/recommend-domain', files={'file': ('bad.exe', b'test')}).status_code, 400)
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/rag/recommend-domain', files={'file': ('file.txt', b'test')}).status_code, 401)

    def test_general_documents_are_available_to_every_agent_but_remain_private(self):
        own = self.upload(domain='general')
        foreign = self.upload(domain='general')
        with self.factory() as db:
            for doc_id, owner, cid in [(own, self.user_id, 'own-chunk'), (foreign, 'another-user', 'foreign-chunk')]:
                doc = db.get(Document, doc_id)
                doc.owner, doc.status, doc.active_preview_id = owner, 'indexed', doc_id
                db.add(Chunk(id=cid, document_id=doc_id, preview_id=doc_id, ordinal=0, text='第一条 通用测试资料。', start=0, end=11, article='第一条', embedding_model='test'))
            db.commit()
        vector = MagicMock()
        vector.query.return_value = {'ids': [['own-chunk', 'foreign-chunk']], 'distances': [[.1, .2]]}
        with patch.object(rag, 'collection', return_value=vector), patch.object(rag, 'embed', AsyncMock(return_value=[[.1, .2]])):
            for domain in ['contract', 'labor', 'family', 'consumer', 'procedure']:
                response = self.client.post('/api/rag/retrieve', json={'query': '通用测试资料', 'domain': domain})
                self.assertEqual(response.status_code, 200)
                self.assertEqual([row['id'] for row in response.json()['items']], ['own-chunk'])
            with self.factory() as db:
                db.get(Document, foreign).visibility = 'public'
                db.commit()
            for domain in ['contract', 'labor', 'family', 'consumer', 'procedure']:
                response = self.client.post('/api/rag/retrieve', json={'query': '通用测试资料', 'domain': domain})
                self.assertEqual([row['id'] for row in response.json()['items']], ['own-chunk', 'foreign-chunk'])
            rows = self.client.get('/api/rag/documents').json()
            self.assertFalse(next(row for row in rows if row['id'] == foreign)['can_manage'])
            self.assertEqual(self.client.delete('/api/rag/documents/' + foreign).status_code, 404)


if __name__ == '__main__':
    unittest.main()
