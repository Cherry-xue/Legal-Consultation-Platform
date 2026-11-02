"""Idempotent additive schema initialization. No customer-facing DDL endpoint."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.db import init_db

if __name__ == '__main__':
    init_db()
    print('Schema initialized; existing legacy records are not assigned to new users.')
