"""Register supplied provenance on exact matching public documents; no reindexing."""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from sqlalchemy import select
from app.db import Document, session_factory

VERSIONS = {'general': '民法典（2020年通过；2021-01-01施行）',
            'labor': '劳动合同法（2012年修正；2013-07-01施行）',
            'consumer': '消费者权益保护法（2013年第二次修正；2014-03-15施行）',
            'procedure': '民事诉讼法（2023年第五次修正；2024-01-01施行）'}
FIELDS = ['source_url', 'version', 'effective_date', 'verified_at', 'legal_status']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    folder = ROOT / 'data' / 'legal-sources'
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    stamp = datetime.now(ZoneInfo('Asia/Shanghai'))
    audit = {'registered_at': stamp.isoformat(), 'scope': '来源与版本登记，未认证截至登记日的最新现行性', 'changes': []}
    with session_factory()() as db:
        for source in manifest['sources']:
            digest = hashlib.sha256((folder / source['file']).read_bytes()).hexdigest()
            assert digest == source['verification']['sha256'], 'Local file differs from source manifest.'
            docs = list(db.scalars(select(Document).where(Document.visibility == 'public', Document.deleted.is_(False),
                                                         Document.name == source['file']).with_for_update()))
            assert len(docs) == 1, 'Expected exactly one matching public document.'
            doc = docs[0]
            assert doc.content_hash == digest, 'Uploaded file differs from the verified local file.'
            assert doc.domain == source['domain'], 'Document domain differs from the manifest.'
            old = {key: getattr(doc, key) for key in FIELDS}
            new = dict(old, source_url=source['source_url'], version=VERSIONS[source['domain']],
                       effective_date=source['effective_date'], verified_at=stamp.strftime('%Y-%m-%d') + ' 来源与版本登记')
            audit['changes'].append(dict(document_id=doc.id, name=doc.name, sha256=digest, before=old, after=new))
            if args.execute:
                for key, value in new.items():
                    setattr(doc, key, value)
        if args.execute:
            # Write the recovery record before committing any metadata changes.
            audit_path = folder / ('metadata-registration-' + stamp.strftime('%Y%m%d-%H%M%S') + '.json')
            audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
            db.commit()
    print(('Registered' if args.execute else 'Validated') + f' {len(audit["changes"])} exact-match public documents; legal status unchanged.')


if __name__ == '__main__':
    main()
