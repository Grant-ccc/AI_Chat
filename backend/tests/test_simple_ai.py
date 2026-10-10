import json
import sqlite3
import pytest
from sqlalchemy import select
from app import config, simple_ai
from app.models import AiReply, Conversation, Message
from app.conversations import add_message, open_handoff
from test_handoff import visitor, merchant, payload


def test_natural_reply_history_and_retry(database, monkeypatch):
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'mock')
    calls = []
    def answer(messages, task_id):
        calls.append(messages)
        return '这是一条自然回复。'
    monkeypatch.setattr(simple_ai, 'generate', answer)
    v, cid = visitor()
    first = payload('自动款')
    assert v.post('/api/visitor/messages', json=first).status_code == 200
    assert v.post('/api/visitor/messages', json=first).status_code == 200
    assert len(calls) == 1
    v.post('/api/visitor/messages', json=payload('教程呢？'))
    assert [m['role'] for m in calls[1]] == ['user', 'assistant', 'user']
    result = v.get('/api/visitor/conversation').json()
    assert result['ai']['status'] == 'complete'
    assert len([m for m in result['messages'] if m['role'] == 'assistant']) == 2
    assert 'prompt' not in json.dumps(result)
    other, _ = visitor()
    assert other.get('/api/visitor/conversation').json()['messages'] == []


@pytest.mark.parametrize('change', ['handoff', 'new_message'])
def test_late_reply_is_discarded(database, monkeypatch, change):
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'mock')
    v, cid = visitor()
    def answer(messages, task_id):
        with database() as db:
            c = db.get(Conversation, cid)
            if change == 'handoff':
                open_handoff(db, c, None, '用户请求', '转人工')
            else:
                add_message(db, c, 'user', '新的问题')
                simple_ai.enqueue(db, c)
            db.commit()
        return '不应发布的旧回复'
    monkeypatch.setattr(simple_ai, 'generate', answer)
    v.post('/api/visitor/messages', json=payload('旧问题'))
    result = v.get('/api/visitor/conversation').json()
    assert all(m['role'] != 'assistant' for m in result['messages'])
    with database() as db:
        assert db.scalar(select(AiReply).where(AiReply.user_sequence == 1)).status == 'stale'


def test_human_pause_and_new_round(database, monkeypatch):
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'mock')
    histories = []
    monkeypatch.setattr(simple_ai, 'generate', lambda messages, _: histories.append(messages) or '自然回复')
    v, cid = visitor()
    v.post('/api/visitor/messages', json=payload('上一轮型号自动款'))
    v.post('/api/visitor/handoff')
    v.post('/api/visitor/messages', json=payload('等待补充'))
    m = merchant()
    root = f'/api/merchant/conversations/{cid}'
    h = m.get(root).json()['handoff']['id']
    m.post(root + '/takeover', json={'handoff_id': h})
    v.post('/api/visitor/messages', json=payload('人工补充'))
    assert len(histories) == 1
    m.post(root + '/messages', json=payload('人工内部处理消息'))
    m.post(root + '/end', json={'handoff_id': h})
    v.post('/api/visitor/messages', json=payload('新一轮多少钱'))
    assert histories[-1] == [{'role': 'user', 'content': '新一轮多少钱'}]
    assert len(v.get('/api/visitor/conversation').json()['messages']) > 5


@pytest.mark.parametrize('error,status', [(RuntimeError('secret-provider-body'), 'failed'),
                                         (simple_ai.LimitError('budget'), 'limited')])
def test_errors_are_explicit_without_leaking(database, monkeypatch, error, status):
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'mock')
    def fail(*args):
        raise error
    monkeypatch.setattr(simple_ai, 'generate', fail)
    v, _ = visitor()
    v.post('/api/visitor/messages', json=payload('问题'))
    result = v.get('/api/visitor/conversation').json()
    assert result['ai']['status'] == status
    assert 'secret-provider-body' not in json.dumps(result)
    assert v.post('/api/visitor/handoff').status_code == 200


def test_restart_does_not_retry(database):
    with database() as db:
        c = Conversation()
        db.add(c)
        db.flush()
        db.add(AiReply(conversation_id=c.id, user_sequence=0, source_revision=0, status='generating'))
        db.commit()
    simple_ai.recover()
    with database() as db:
        assert db.scalar(select(AiReply)).status == 'failed'


def test_prompt_is_approved_public_template():
    result = simple_ai.system_prompt()
    assert '{{' not in result
    assert '单面外花窗有自动款' in result
    assert '额外留一周' not in result and '内部另留一周' not in result
    assert 'T21' not in result and '参考答案' not in result
    assert '2026-05-01 10:00 至 2026-05-05 23:59' in result


def test_paid_transport_count_persistence_and_failure_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(simple_ai, 'ROOT', tmp_path)
    monkeypatch.setattr(config, 'AI_WEB_MAX_CALLS', 1)
    monkeypatch.setattr(config, 'AI_WEB_BUDGET_CNY', 1.0)
    requests = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            requests.append(kwargs['json'])
            import httpx
            return httpx.Response(200, request=httpx.Request('POST', url), json={
                'choices': [{'finish_reason': 'stop', 'message': {'content': '自然回复'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})
    monkeypatch.setattr(simple_ai.httpx, 'Client', Client)
    request = {'messages': [{'role': 'user', 'content': '问题'}]}
    assert simple_ai.ledger_call(request, 'first', 'local-test-secret') == '自然回复'
    with pytest.raises(simple_ai.LimitError):
        simple_ai.ledger_call(request, 'second', 'local-test-secret')
    assert len(requests) == 1
    path = tmp_path / '.local/simple-web/ledger.sqlite3'
    assert b'local-test-secret' not in path.read_bytes()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE calls SET status='failed_or_unknown'")
    monkeypatch.setattr(config, 'AI_WEB_MAX_CALLS', 12)
    with pytest.raises(simple_ai.LimitError):
        simple_ai.ledger_call(request, 'third', 'local-test-secret')


def test_paid_budget_zero_stops_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(simple_ai, 'ROOT', tmp_path)
    monkeypatch.setattr(config, 'AI_WEB_BUDGET_CNY', 0)
    with pytest.raises(simple_ai.LimitError):
        simple_ai.ledger_call({'messages': [{'role': 'user', 'content': '问题'}]}, 'call', 'unused')


def test_unlimited_calls_and_budget_keep_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(simple_ai, 'ROOT', tmp_path)
    monkeypatch.setattr(config, 'AI_WEB_MAX_CALLS', -1)
    monkeypatch.setattr(config, 'AI_WEB_BUDGET_CNY', -1)
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            import httpx
            return httpx.Response(200, request=httpx.Request('POST', url), json={
                'choices': [{'finish_reason': 'stop', 'message': {'content': '自然回复'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})
    monkeypatch.setattr(simple_ai.httpx, 'Client', Client)
    request = {'messages': [{'role': 'user', 'content': '问题'}]}
    for i in range(15):
        assert simple_ai.ledger_call(request, str(i), 'test-key') == '自然回复'
    with sqlite3.connect(tmp_path / '.local/simple-web/ledger.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 15
        db.execute("UPDATE calls SET status='failed_or_unknown' WHERE id='0'")
    with pytest.raises(simple_ai.LimitError):
        simple_ai.ledger_call(request, 'failed-guard', 'test-key')


@pytest.mark.parametrize('kind', ['http_error', 'truncated', 'usage_overflow'])
def test_bad_paid_results_pause_without_retry(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(simple_ai, 'ROOT', tmp_path)
    monkeypatch.setattr(config, 'AI_WEB_MAX_CALLS', 12)
    monkeypatch.setattr(config, 'AI_WEB_BUDGET_CNY', 1)
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            calls.append(url)
            import httpx
            return httpx.Response(503 if kind == 'http_error' else 200,
                request=httpx.Request('POST', url), json={
                    'choices': [{'finish_reason': 'length' if kind == 'truncated' else 'stop',
                                 'message': {'content': '不应发布'}}],
                    'usage': {'prompt_tokens': 1_000_000 if kind == 'usage_overflow' else 10,
                              'completion_tokens': 5}})
    monkeypatch.setattr(simple_ai.httpx, 'Client', Client)
    request = {'messages': [{'role': 'user', 'content': '问题'}]}
    with pytest.raises(Exception):
        simple_ai.ledger_call(request, 'first', 'test-key')
    with pytest.raises(simple_ai.LimitError):
        simple_ai.ledger_call(request, 'second', 'test-key')
    assert len(calls) == 1


def test_expired_price_stops_before_call(monkeypatch):
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'deepseek')
    monkeypatch.setattr(config, 'AI_PRICE_DATE', '2000-01-01')
    with pytest.raises(simple_ai.LimitError):
        simple_ai.generate([{'role': 'user', 'content': '问题'}], 'no-call')
