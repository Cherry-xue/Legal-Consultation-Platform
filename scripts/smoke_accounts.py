"""Real MySQL account/isolation smoke test using disposable app users, no model calls."""
import argparse
import io
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--admin-env')
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute creates and cleans disposable app accounts in legal_assistant.')
    if args.admin_env:
        from dotenv import dotenv_values
        source = dotenv_values(args.admin_env, encoding='utf-8-sig')
        for target, origin in [('MYSQL_HOST', 'MYSQL_HOST'), ('MYSQL_PORT', 'MYSQL_PORT'),
                               ('MYSQL_USER', 'MYSQL_USER_ADMIN'), ('MYSQL_PASSWORD', 'MYSQL_PASSWORD_ADMIN')]:
            os.environ[target] = source[origin]
    os.environ['MYSQL_DATABASE'] = 'legal_assistant'
    from fastapi.testclient import TestClient
    from PIL import Image
    from sqlalchemy import delete, select
    from app.main import app
    from app.db import ChatMessage, Chunk, Conversation, Document, Job, Preview, User, UserMemory, UserSession, session_factory
    from app.config import settings
    users, clients = [], [TestClient(app), TestClient(app)]
    try:
        for client in clients:
            response = client.post('/api/auth/register', json={'username': 'acct_' + secrets.token_hex(5),
                                                               'password': secrets.token_urlsafe(20)})
            assert response.status_code == 200, 'Registration failed'
            users.append(response.json()['id'])
        a, b = clients
        memory = a.post('/api/memories', json={'content': 'Disposable test preference.'}).json()
        conversation = a.post('/api/conversations', json={'agent_code': 'contract'}).json()
        assert b.get('/api/memories').json() == []
        assert b.get('/api/conversations').json() == []
        assert b.delete('/api/memories/' + memory['id']).status_code == 404
        assert b.get('/api/conversations/' + conversation['id'] + '/messages').status_code == 404
        document = a.post('/api/rag/documents', files={'file': ('account-smoke.txt', b'Disposable private fixture')})
        assert document.status_code == 200
        assert b.get('/api/rag/documents').json() == []
        assert b.delete('/api/rag/documents/' + document.json()['id']).status_code == 404
        image = io.BytesIO(); Image.new('RGB', (300, 180), '#27543f').save(image, 'PNG')
        avatar = a.post('/api/auth/avatar', files={'file': ('avatar.png', image.getvalue(), 'image/png')})
        assert avatar.status_code == 200
        response = a.get(avatar.json()['avatar_url'])
        assert Image.open(io.BytesIO(response.content)).size == (256, 256)
        assert b.get('/api/auth/avatar').status_code == 404
        assert a.post('/api/auth/logout').status_code == 200
        assert a.get('/api/auth/me').status_code == 401
        print('PASS: real MySQL registration, cookie sessions, private memories/conversations/materials, avatar processing, logout.')
    finally:
        with session_factory()() as db:
            for user_id in users:
                doc_ids = list(db.scalars(select(Document.id).where(Document.owner == user_id)))
                for doc in db.scalars(select(Document).where(Document.owner == user_id)):
                    (settings().storage / doc.original_path).unlink(missing_ok=True)
                if doc_ids:
                    for entity in [Chunk, Preview, Job]:
                        db.execute(delete(entity).where(entity.document_id.in_(doc_ids)))
                    db.execute(delete(Document).where(Document.owner == user_id))
                conv_ids = list(db.scalars(select(Conversation.id).where(Conversation.owner == user_id)))
                if conv_ids:
                    db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conv_ids)))
                    db.execute(delete(Conversation).where(Conversation.owner == user_id))
                db.execute(delete(UserMemory).where(UserMemory.owner == user_id))
                db.execute(delete(UserSession).where(UserSession.user_id == user_id))
                user = db.get(User, user_id)
                if user and user.avatar_revision:
                    (settings().storage / 'avatars' / f'{user.id}_{user.avatar_revision}.jpg').unlink(missing_ok=True)
                if user:
                    db.delete(user)
            db.commit()
        for client in clients:
            client.close()
        session_factory().kw['bind'].dispose()
        print('Disposable app accounts and test materials cleaned.')


if __name__ == '__main__':
    main()
