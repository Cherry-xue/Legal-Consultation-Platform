"""Create local-only secrets without printing or copying the shared provider key."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / '.env'
if target.exists():
    print('Existing project .env preserved.')
else:
    template = (root / '.env.example').read_text(encoding='utf-8')
    template = template.replace('replace-me-too', secrets.token_urlsafe(24)).replace('replace-me', secrets.token_urlsafe(24))
    target.write_text(template, encoding='utf-8')
    print('Created project .env with random database passwords. Provider credentials stay in the shared file.')
