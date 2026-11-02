"""Compare two retrieval strategies on identical vectors and an isolated account."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import secrets
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from evaluate_legal_baseline import load_cases


async def main():
    from sqlalchemy import delete
    from app import rag
    from app.auth import DUMMY_HASH
    from app.config import settings
    from app.db import User, RetrievalLog, session_factory, uid
    assert settings().mysql_database == 'legal_assistant'
    cases = load_cases('extended')
    probes = [dict(id=c['id'], domain=c['agent'], query=c['queries'][0], file=c['file'], article=c['article']) for c in cases]
    probes += [dict(id='NEG-HELLO', domain='contract', query='你好，谢谢', file=None, article=None),
               dict(id='NEG-SPACE', domain='contract', query='如何设计火星探测器光谱仪的激光波长和探测器电路', file=None, article=None),
               dict(id='NEG-ARTICLE', domain='contract', query='民法典第9999条的原文是什么', file=None, article=None)]
    with session_factory()() as db:
        user = User(username='rankeval_' + secrets.token_hex(6), display_name='检索验收临时账号', password_hash=DUMMY_HASH)
        db.add(user); db.commit(); owner = user.id
    batch = uid()
    cache, report = {}, {'cases': []}
    original = rag.embed
    async def cached_embed(texts):
        key = tuple(texts)
        if key not in cache:
            cache[key] = await original(texts)
        return cache[key]
    output = ROOT / 'data/eval-reports'
    output.mkdir(exist_ok=True)
    path = output / ('retrieval-comparison-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    try:
        for enabled in (False, True):
            config = settings().model_copy(update={'rag_hybrid_enabled': enabled})
            gate = asyncio.Semaphore(3)
            async def one(probe):
                async with gate:
                    try:
                        with session_factory()() as db:
                            hits = await rag.retrieve(db, probe['query'], probe['domain'], owner=owner, run_id=batch)
                        expected = [i+1 for i,h in enumerate(hits) if h['document_name'] == probe['file'] and h['article'] == probe['article']]
                        result = dict(**probe, strategy='hybrid' if enabled else 'vector', expected_rank=min(expected) if expected else None, hits=hits)
                    except Exception as error:
                        result = dict(**probe, strategy='hybrid' if enabled else 'vector', error=getattr(error,'code',type(error).__name__), hits=[])
                    report['cases'].append(result)
                    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            with patch.object(rag, 'settings', return_value=config), patch.object(rag, 'embed', cached_embed):
                await asyncio.gather(*(one(probe) for probe in probes))
            print('Finished', 'hybrid' if enabled else 'vector', len(probes), flush=True)
    finally:
        with session_factory()() as db:
            db.execute(delete(RetrievalLog).where(RetrievalLog.run_id == batch))
            db.execute(delete(User).where(User.id == owner)); db.commit()
    report['summary'] = {}
    for strategy in ('vector','hybrid'):
        rows = [r for r in report['cases'] if r['strategy']==strategy]
        positives = [r for r in rows if r['file']]
        report['summary'][strategy] = dict(queries=len(rows), hit_at_5=sum(bool(r['expected_rank']) for r in positives),
            hit_at_1=sum(r['expected_rank']==1 for r in positives), mrr=round(sum(1/r['expected_rank'] if r['expected_rank'] else 0 for r in positives)/len(positives),4),
            negative_results={r['id']:len(r['hits']) for r in rows if not r['file']}, errors=sum('error' in r for r in rows))
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['summary']),flush=True)
    print('Report:',path,flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true',required=True)
    parser.parse_args()
    asyncio.run(main())
