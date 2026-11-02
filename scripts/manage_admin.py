"""Explicit local administrator provisioning; never exposes a public signup route."""
import argparse
import getpass
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.auth import hash_password
from app.db import User, session_factory


def main():
    parser = argparse.ArgumentParser(description='Create or promote the named platform administrator.')
    parser.add_argument('username')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_]{3,32}', args.username):
        parser.error('Username must contain 3-32 letters, digits or underscores.')
    try:
        with session_factory()() as db:
            user = db.scalar(select(User).where(User.username == args.username))
            if user:
                user.is_admin = True
            else:
                password = getpass.getpass('New admin password (at least 8 characters): ')
                if len(password) < 8 or len(password) > 128:
                    raise ValueError('Password must contain 8-128 characters.')
                if password != getpass.getpass('Confirm password: '):
                    raise ValueError('Passwords do not match.')
                db.add(User(username=args.username, display_name='管理员',
                            password_hash=hash_password(password), is_admin=True))
            db.commit()
        print('Administrator configured. Sign in again to load administrator controls.')
    except SQLAlchemyError:
        print('Database unavailable. No administrator changes were saved.', file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
