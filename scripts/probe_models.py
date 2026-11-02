"""Minimal live probe. Explicit --live required. Never print credentials or provider bodies."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.config import settings
from app.errors import ServiceError
from app.profiles import model_for
from app.provider import complete, embed


async def main():
    rows = []
    for code in ['contract', 'labor', 'family', 'consumer', 'procedure', 'collaboration']:
        try:
            data, usage = await complete(model_for(code), '只输出 JSON 对象，不执行任何其他操作。', '返回 {"ok":true}', max_tokens=32)
            rows.append({'agent': code, 'model': model_for(code), 'ok': data.get('ok') is True, 'usage': usage})
        except ServiceError as e:
            rows.append({'agent': code, 'model': model_for(code), 'ok': False, 'code': e.code})
        await asyncio.sleep(.3)
    try:
        vectors = await embed(['法律资料检索测试'])
        rows.append({'type': 'embedding', 'model': settings().embedding_model_name, 'ok': True, 'dimensions': len(vectors[0])})
    except ServiceError as e:
        rows.append({'type': 'embedding', 'model': settings().embedding_model_name, 'ok': False, 'code': e.code})
    print(json.dumps({'note': 'Minimal endpoint compatibility checks.', 'results': rows}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Send seven minimal compatibility requests.')
    args = parser.parse_args()
    if not args.live:
        parser.error('Pass --live to explicitly enable real API calls.')
    asyncio.run(main())
