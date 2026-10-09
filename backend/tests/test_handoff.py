from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.main import app
from app import config
from app.models import Conversation, Handoff, VisitorSession, now
from app.conversations import add_message, open_handoff, latest_handoff
from app.restore_manual_choice import restore_manual_choice
from datetime import timedelta


def visitor():
    c = TestClient(app)
    c.headers['Origin'] = config.APP_ORIGIN
    r = c.post('/api/visitor/session').json()
    c.headers['x-csrf-token'] = r['csrf']
    return c, r['conversation_id']


def merchant():
    c = TestClient(app)
    c.headers['Origin'] = config.APP_ORIGIN
    r = c.post('/api/merchant/login', json={'username': 'merchant', 'password': 'integration-password'}).json()
    c.headers['x-csrf-token'] = r['csrf']
    return c


def payload(content, identity=None):
    return {'content': content, 'client_message_id': identity or str(uuid4())}


def test_full_loop_and_private_data(database):
    v, cid = visitor()
    other, _ = visitor()
    m = merchant()
    root = f'/api/merchant/conversations/{cid}'
    assert other.get(root).status_code == 401
    v.post('/api/visitor/messages', json=payload('我想问操作问题'))
    for _ in range(2):
        assert v.post('/api/visitor/handoff').status_code == 200
    detail = m.get(root).json()
    h = detail['handoff']['id']
    assert m.get('/api/merchant/conversations').json()['pending'][0]['unread']
    assert m.post(root+'/messages', json=payload('未接手')).status_code == 409
    assert m.post(root+'/end', json={'handoff_id': h}).status_code == 409
    m.post(root+'/read', json={'handoff_id': h, 'sequence': detail['sequence']})
    assert m.get(root).json()['status'] == 'waiting_human'
    assert not m.get('/api/merchant/conversations').json()['pending'][0]['unread']
    v.post('/api/visitor/messages', json=payload('补充一句'))
    assert m.get('/api/merchant/conversations').json()['pending'][0]['unread']
    m.post(root+'/takeover', json={'handoff_id': h})
    before = m.get(root).json()['sequence']
    m.post(root+'/takeover', json={'handoff_id': h})
    assert m.get(root).json()['sequence'] == before
    send = payload('商家回复')
    assert m.post(root+'/messages', json=send).status_code == 200
    m.post(root+'/messages', json=send)
    assert sum(x['content']=='商家回复' for x in v.get('/api/visitor/conversation').json()['messages']) == 1
    assert 'handoff' not in v.get('/api/visitor/conversation').json()
    assert other.get('/api/visitor/conversation').json()['messages'] == []
    m.post(root+'/end', json={'handoff_id': h})
    assert len(m.get('/api/merchant/conversations').json()['ended']) == 1
    assert m.post(root+'/messages', json=payload('已结束')).status_code == 409
    v.post('/api/visitor/messages', json=payload('新问题'))
    assert v.get('/api/visitor/conversation').json()['status'] == 'ai_ready'
    assert len(m.get('/api/merchant/conversations').json()['ended']) == 1
    assert len(m.get('/api/merchant/conversations').json()['pending']) == 0
    v.post('/api/visitor/handoff')
    new = m.get(root).json()['handoff']
    assert new['round'] == 2 and new['id'] != h
    assert m.post(root+'/takeover', json={'handoff_id': h}).status_code == 409
    assert len(m.get('/api/merchant/conversations').json()['pending']) == 1
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Handoff)) == 2
        assert db.get(Handoff, h).ended_at is not None


def test_no_history_and_concurrent_duplicate_handoff(database):
    v, cid = visitor()
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: v.post('/api/visitor/handoff').status_code, range(2)))
    assert codes == [200, 200]
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Handoff)) == 1
    detail = merchant().get(f'/api/merchant/conversations/{cid}').json()
    assert detail['handoff']['reason'] == '用户主动请求人工'
    assert detail['handoff']['summary_status'] == 'not_connected'


def test_security_validation_expiry_and_send_retry(database):
    v, cid = visitor()
    data = payload('测试')
    assert v.post('/api/visitor/messages', json={**data, 'role': 'merchant'}).status_code == 422
    assert v.post('/api/visitor/messages', json=payload(' ')).status_code == 422
    assert v.post('/api/visitor/messages', json=payload('x'*2001)).status_code == 422
    assert v.post('/api/visitor/messages', json=data, headers={'x-csrf-token': 'bad'}).status_code == 403
    assert v.post('/api/visitor/messages', json=data).status_code == 200
    assert v.post('/api/visitor/messages', json=data).status_code == 200
    assert v.post('/api/visitor/messages', json={**data,'content':'不同内容'}).status_code == 409
    assert len(v.get('/api/visitor/conversation').json()['messages']) == 1
    with database() as db:
        identity = db.scalar(select(VisitorSession).where(VisitorSession.conversation_id == cid))
        identity.expires_at = now()-timedelta(seconds=1)
        db.commit()
    assert v.get('/api/visitor/conversation').status_code == 401
    assert v.post('/api/visitor/session').status_code == 401


def test_concurrent_sends_and_read_only_visible_sequence(database):
    v, cid = visitor()
    first = payload('同一消息并发重试')
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: v.post('/api/visitor/messages', json=first).status_code, range(2)))
    assert codes == [200, 200]
    v.post('/api/visitor/handoff')
    m = merchant()
    root = f'/api/merchant/conversations/{cid}'
    visible = m.get(root).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda data: v.post('/api/visitor/messages', json=data), [payload('补充A'), payload('补充B')]))
    assert all(reply.status_code == 200 for reply in replies)
    # A read acknowledgment made from the earlier render must not consume new messages.
    m.post(root+'/read', json={'handoff_id': visible['handoff']['id'], 'sequence': visible['sequence']})
    assert m.get('/api/merchant/conversations').json()['pending'][0]['unread']
    latest = m.get(root).json()
    assert [message['sequence'] for message in latest['messages']] == [1, 2, 3, 4]
    assert len({message['id'] for message in latest['messages']}) == 4
    m.post(root+'/read', json={'handoff_id': latest['handoff']['id'], 'sequence': latest['sequence']})
    assert not m.get('/api/merchant/conversations').json()['pending'][0]['unread']


def test_new_consultation_requires_explicit_handoff(database):
    v, cid = visitor()
    old_message = payload('第一轮问题')
    v.post('/api/visitor/messages', json=old_message)
    v.post('/api/visitor/handoff')
    m = merchant()
    root = f'/api/merchant/conversations/{cid}'
    old_handoff = m.get(root).json()['handoff']['id']
    m.post(root+'/takeover', json={'handoff_id': old_handoff})
    m.post(root+'/end', json={'handoff_id': old_handoff})
    # A retry of a message from the ended round is not a new consultation.
    v.post('/api/visitor/messages', json=old_message)
    assert v.get('/api/visitor/conversation').json()['status'] == 'ended'
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda data: v.post('/api/visitor/messages', json=data).status_code,
                              [payload('新的问题A'), payload('新的问题B')]))
    assert codes == [200, 200]
    detail = m.get(root).json()
    assert detail['status'] == 'ai_ready' and detail['handoff']['round'] == 1
    assert m.post(root+'/messages', json=payload('还没接手')).status_code == 409
    assert m.post(root+'/takeover', json={'handoff_id': old_handoff}).status_code == 409
    assert m.get('/api/merchant/conversations').json()['pending'] == []
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: v.post('/api/visitor/handoff').status_code, range(2)))
    assert codes == [200, 200]
    assert m.get(root).json()['handoff']['round'] == 2
    assert len(m.get('/api/merchant/conversations').json()['pending']) == 1
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Handoff)) == 2
        assert db.get(Handoff, old_handoff).ended_at is not None


def test_restore_only_unclaimed_automatic_handoffs(database):
    v, cid = visitor()
    explicit, explicit_id = visitor()
    explicit.post('/api/visitor/handoff')
    v.post('/api/visitor/handoff')
    m = merchant()
    root = f'/api/merchant/conversations/{cid}'
    old_handoff = m.get(root).json()['handoff']['id']
    m.post(root+'/takeover', json={'handoff_id': old_handoff})
    m.post(root+'/end', json={'handoff_id': old_handoff})
    with database() as db:
        c = db.get(Conversation, cid)
        add_message(db, c, 'user', '旧版本结束后补充的问题')
        open_handoff(db, c, latest_handoff(db, c), '上一轮结束后用户继续咨询', '旧版本自动交接')
        db.commit()
    automatic_id = m.get(root).json()['handoff']['id']
    active, active_id = visitor()
    with database() as db:
        c = db.get(Conversation, active_id)
        open_handoff(db, c, None, '上一轮结束后用户继续咨询', '旧版本自动交接')
        db.commit()
    active_root = f'/api/merchant/conversations/{active_id}'
    active_h = m.get(active_root).json()['handoff']['id']
    m.post(active_root+'/takeover', json={'handoff_id': active_h})
    with database() as db:
        assert restore_manual_choice(db) == 1
        db.commit()
    with database() as db:
        assert restore_manual_choice(db) == 0
        assert db.get(Conversation, explicit_id).status == 'waiting_human'
        assert db.get(Conversation, active_id).status == 'human_active'
        assert db.get(Handoff, automatic_id).ended_at is not None
        assert db.get(Handoff, old_handoff).ended_at is not None
    detail = m.get(root).json()
    assert detail['status'] == 'ai_ready'
    assert any(message['content'] == '旧版本结束后补充的问题' for message in detail['messages'])
    v.post('/api/visitor/handoff')
    assert m.get(root).json()['status'] == 'waiting_human'
