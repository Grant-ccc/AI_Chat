from fastapi.testclient import TestClient
from app.main import app
from app import config
from app.models import Conversation, VisitorSession, Merchant
from app.security import digest, attempts
from sqlalchemy import select, func


def test_visitor_session_refresh_and_origin(database):
    a = TestClient(app)
    b = TestClient(app)
    assert a.post('/api/visitor/session', headers={'Origin': 'https://evil.example'}).status_code == 403
    headers = {'Origin': config.APP_ORIGIN}
    first = a.post('/api/visitor/session', headers=headers)
    assert first.status_code == 200 and 'HttpOnly' in first.headers['set-cookie']
    assert a.post('/api/visitor/session', headers=headers).json()['conversation_id'] == first.json()['conversation_id']
    assert b.post('/api/visitor/session', headers=headers).json()['conversation_id'] != first.json()['conversation_id']
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Conversation)) == 2
        stored = db.scalar(select(VisitorSession))
        assert stored.token_hash != a.cookies.get('umbrella_visitor')


def test_merchant_login_csrf_and_logout(database):
    c = TestClient(app)
    origin = {'Origin': config.APP_ORIGIN}
    assert c.get('/api/merchant/session').status_code == 401
    assert c.post('/api/merchant/login', headers=origin, json={'username': 'merchant', 'password': 'wrong'}).status_code == 401
    result = c.post('/api/merchant/login', headers=origin, json={'username': 'merchant', 'password': 'integration-password'})
    assert result.status_code == 200
    assert c.post('/api/merchant/logout', headers=origin).status_code == 403
    csrf = result.json()['csrf']
    assert c.post('/api/merchant/logout', headers={**origin, 'x-csrf-token': csrf}).status_code == 200
    assert c.get('/api/merchant/session').status_code == 401
    with database() as db:
        assert 'integration-password' not in db.get(Merchant, 'merchant').password_hash


def test_login_rate_limit(database):
    c = TestClient(app)
    for _ in range(5):
        assert c.post('/api/merchant/login', headers={'Origin': config.APP_ORIGIN}, json={'username': 'merchant', 'password': 'bad'}).status_code == 401
    assert c.post('/api/merchant/login', headers={'Origin': config.APP_ORIGIN}, json={'username': 'merchant', 'password': 'bad'}).status_code == 429
