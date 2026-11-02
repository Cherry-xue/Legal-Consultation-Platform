import unittest
from types import SimpleNamespace as Row
from app.search_rank import article_refs, rank_candidates, lexical_scores
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select
from test_core import SQLFixture
from app import rag
from app.db import Chunk, Document


def chunk(key, text, article='', document='law'):
    return Row(id=key, document_id=document, text=text, article=article)


class SearchRankingTests(unittest.TestCase):
    def test_article_numerals_normalize_without_confusing_subarticles(self):
        self.assertEqual(article_refs('第五百六十二条'), article_refs('第562条'))
        self.assertNotEqual(article_refs('第六条之一'), article_refs('第六条之二'))

    def test_exact_reference_is_recalled_even_when_both_candidate_lists_miss(self):
        rows = [chunk(f'noise{i}', '民法典 总则资料。', '第一条') for i in range(40)]
        rows.append(chunk('target', '当事人协商一致，可以解除合同。', '第五百六十二条'))
        result = rank_candidates('民法典第562条', rows, {'law': '中华人民共和国民法典'}, {}, candidate_limit=10)
        self.assertEqual([r['id'] for r in result], ['target'])
        self.assertIn('article', result[0]['channels'])

    def test_wrong_law_or_body_cross_reference_cannot_satisfy_explicit_heading(self):
        rows = [chunk('a', '依照第五百六十二条执行。', '第五百六十三条'),
                chunk('b', '协商解除。', '第五百六十二条', 'other')]
        self.assertEqual(rank_candidates('民法典第562条', rows, {'law':'民法典','other':'其他法规'}, {'a':.05,'b':.05}), [])

    def test_keyword_can_rescue_vector_miss_and_bm25_is_not_plain_frequency(self):
        rows = [chunk('target', '采购设备验收完成后支付质量保证金。'), chunk('other', '诉讼案件材料。')]
        result = rank_candidates('采购设备验收质量保证金', rows, {'law':'资料'}, {'other':.8})
        self.assertEqual(result[0]['id'], 'target')
        self.assertEqual(result[0]['channels'], ['keyword'])
        scores = lexical_scores('质量保证金', {'short':'质量保证金支付', 'long':'质量保证金支付'+'普通文字'*1000})
        self.assertGreater(scores['short'][0], scores['long'][0])

    def test_vector_supports_paraphrase_and_weak_matches_do_not_fill_top_k(self):
        rows = [chunk('a', '当事人协议终止交易。'), chunk('b','民事诉讼费用。')]
        result = rank_candidates('两方同意结束买卖', rows, {'law':'资料'}, {'a':.2,'b':.9})
        self.assertEqual([r['id'] for r in result], ['a'])
        self.assertEqual(rank_candidates('星际光谱探测', rows, {'law':'资料'}, {'a':.9,'b':float('nan')}), [])

    def test_rerank_prefers_multi_signal_match_over_vector_only_candidate(self):
        rows = [chunk('a','质量保证金应在设备验收完成后支付。'),chunk('b','争议解决程序。')]
        result = rank_candidates('质量保证金验收支付',rows,{'law':'采购合同'},{'a':.2,'b':.18})
        self.assertEqual(result[0]['id'],'a')
        self.assertEqual(set(result[0]['channels']),{'keyword','vector'})


class HybridIntegrationTests(SQLFixture):
    def seed(self):
        own = self.upload('第一条 设备验收后支付质量保证金。')
        foreign = self.upload('第一条 星际光谱仪激光电路。')
        with self.factory() as db:
            for doc_id, owner, text in [(own, self.user_id, '设备验收后支付质量保证金。'), (foreign, 'another-user', '星际光谱仪激光电路。')]:
                doc = db.get(Document, doc_id)
                doc.owner, doc.status, doc.active_preview_id = owner, 'indexed', doc_id
                db.add(Chunk(id=doc_id, document_id=doc_id, preview_id=doc_id, ordinal=0,
                             text=text, start=0, end=len(text), article='第一条', embedding_model='test'))
            db.commit()
        return own, foreign

    def test_keyword_rescue_and_no_unauthorized_candidates_or_forced_weak_results(self):
        own, foreign = self.seed()
        vector = MagicMock()
        vector.query.return_value = {'ids': [[foreign, own]], 'distances': [[.01, .9]]}
        with patch.object(rag,'collection',return_value=vector), patch.object(rag,'embed',AsyncMock(return_value=[[.1,.2]])):
            found = self.client.post('/api/rag/retrieve',json={'query':'设备验收质量保证金','domain':'contract'})
            self.assertEqual([r['id'] for r in found.json()['items']], [own])
            self.assertIn('keyword', found.json()['items'][0]['ranking']['channels'])
            filtered = self.client.post('/api/rag/retrieve',json={'query':'星际光谱仪激光电路','domain':'contract'})
            self.assertEqual(filtered.json()['items'], [])

    def test_vector_outage_degrades_to_keywords_but_not_false_empty_success(self):
        own, _ = self.seed()
        with patch.object(rag,'embed',AsyncMock(side_effect=RuntimeError('unavailable'))):
            found = self.client.post('/api/rag/retrieve',json={'query':'设备验收质量保证金','domain':'contract'})
            self.assertEqual(found.status_code,200)
            self.assertEqual(found.json()['items'][0]['id'],own)
            self.assertTrue(found.json()['items'][0]['retrieval_degraded'])
            failed = self.client.post('/api/rag/retrieve',json={'query':'星际光谱仪激光电路','domain':'contract'})
            self.assertEqual(failed.status_code,503)
