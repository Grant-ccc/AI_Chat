"""真实MySQL事务与模拟生成：不连接模型服务，不使用正常应用库。"""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from sqlalchemy import select, func
from app import ai_review, config
from app.models import AiReview, Conversation, Message
from test_handoff import visitor, merchant, payload


@pytest.fixture
def review_db(database, monkeypatch):
    monkeypatch.setattr(ai_review, 'SessionLocal', database)
    monkeypatch.setattr(config, 'AI_REVIEW_MODE', 'mock')
    monkeypatch.setattr(config, 'AI_REVIEW_PAID_APPROVED', False)
    return database


def draft_for(m, cid):
    return m.get(f'/api/merchant/conversations/{cid}').json()['review']


def decide(m, cid, draft, action='approve', content='已核对，这是商家确认的回复。'):
    return m.post(f'/api/merchant/conversations/{cid}/reviews/{draft["id"]}/{action}',
                  json={'content': content, 'confirmed': True})


def test_review_private_edit_and_concurrent_idempotency(review_db, monkeypatch):
    calls = []
    original = ai_review.generate
    def counted(*args):
        calls.append(args[0])
        return original(*args)
    monkeypatch.setattr(ai_review, 'generate', counted)
    v, cid = visitor()
    other, _ = visitor()
    m = merchant()
    data = payload('双面款多少钱？')
    assert v.post('/api/visitor/messages', json=data).status_code == 200
    assert v.post('/api/visitor/messages', json=data).status_code == 200
    public = v.get('/api/visitor/conversation').json()
    assert public['ai']['phase'] == 'ready'
    assert 'review' not in public and '模拟联调候选' not in json.dumps(public, ensure_ascii=False)
    assert len(public['messages']) == 1 and len(calls) == 1
    assert other.get('/api/visitor/conversation').json()['messages'] == []
    assert len(m.get('/api/merchant/conversations').json()['reviews']) == 1
    draft = draft_for(m, cid)
    assert draft['status'] == 'ready' and draft['mode'] == 'mock'
    path = f'/api/merchant/conversations/{cid}/reviews/{draft["id"]}/approve'
    assert v.post(path, json={'content': '越权', 'confirmed': True}).status_code == 401
    assert m.post(path, json={'content': '未确认'}).status_code == 422
    assert m.post(path, json={'content': ' ', 'confirmed': True}).status_code == 422
    assert m.post(path, json={'content': '测试', 'confirmed': True}, headers={'x-csrf-token': 'bad'}).status_code == 403
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: decide(m, cid, draft).status_code, range(2)))
    assert codes == [200, 200]
    public = v.get('/api/visitor/conversation').json()
    assert sum(x['role'] == 'assistant' for x in public['messages']) == 1
    assert public['messages'][-1]['content'] == '已核对，这是商家确认的回复。'
    assert decide(m, cid, draft, content='改掉已发送内容').status_code == 409
    assert m.get('/api/merchant/conversations').json()['reviews'] == []
    assert m.get('/api/merchant/conversations').json()['ended'][0]['review_status'] == 'approved'
    with review_db() as db:
        assert db.scalar(select(func.count()).select_from(AiReview)) == 1
        assert db.get(AiReview, draft['id']).reviewed_by == 'merchant'


@pytest.mark.parametrize('change', ['handoff', 'new_message'])
def test_late_result_cannot_leak_or_approve(review_db, monkeypatch, change):
    entered, release = Event(), Event()
    original = ai_review.generate
    def delayed(*args):
        if args[3] == '第一条问题':
            entered.set()
            assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(ai_review, 'generate', delayed)
    v, cid = visitor()
    m = merchant()
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(lambda: v.post('/api/visitor/messages', json=payload('第一条问题')))
        assert entered.wait(10)
        draft = draft_for(m, cid)
        try:
            if change == 'handoff':
                assert v.post('/api/visitor/handoff').status_code == 200
            else:
                assert v.post('/api/visitor/messages', json=payload('第二条问题')).status_code == 200
        finally:
            release.set()
        assert old.result().status_code == 200
    assert decide(m, cid, draft).status_code == 409
    public = v.get('/api/visitor/conversation').json()
    assert all(x['role'] != 'assistant' for x in public['messages'])
    with review_db() as db:
        assert db.get(AiReview, draft['id']).status == 'stale'


@pytest.mark.parametrize('result,expected', [({'status': 'timeout'}, 'failed'),
    ({'status': 'complete', 'finish_reason': 'length', 'content': 'secret-output'}, 'invalid'),
    ({'status': 'complete', 'finish_reason': 'stop', 'content': 'secret-output'}, 'invalid')])
def test_bad_outputs_cannot_send_and_can_handoff(review_db, monkeypatch, result, expected):
    monkeypatch.setattr(ai_review, 'generate', lambda *args: result)
    v, cid = visitor()
    m = merchant()
    v.post('/api/visitor/messages', json=payload('问题'))
    draft = draft_for(m, cid)
    assert draft['status'] == expected
    assert draft['candidate'] is None
    assert 'secret-output' not in json.dumps(v.get('/api/visitor/conversation').json())
    assert decide(m, cid, draft).status_code == 409
    assert decide(m, cid, draft, action='handoff').status_code == 200
    assert decide(m, cid, draft, action='handoff').status_code == 200
    queue = m.get('/api/merchant/conversations').json()
    assert len(queue['pending']) == 1 and queue['reviews'] == []


def test_reject_then_handoff_and_new_round_resets(review_db):
    v, cid = visitor()
    m = merchant()
    v.post('/api/visitor/messages', json=payload('第一轮'))
    draft = draft_for(m, cid)
    assert decide(m, cid, draft, 'reject').status_code == 200
    assert decide(m, cid, draft).status_code == 409
    assert decide(m, cid, draft, 'handoff').status_code == 200
    root = f'/api/merchant/conversations/{cid}'
    h = m.get(root).json()['handoff']['id']
    m.post(root + '/takeover', json={'handoff_id': h})
    v.post('/api/visitor/messages', json=payload('人工期间不生成'))
    with review_db() as db:
        assert db.scalar(select(func.count()).select_from(AiReview)) == 1
    m.post(root + '/end', json={'handoff_id': h})
    v.post('/api/visitor/messages', json=payload('第二轮'))
    public = v.get('/api/visitor/conversation').json()
    assert public['status'] == 'ai_ready' and public['ai']['phase'] == 'ready'
    assert draft_for(m, cid)['id'] != draft['id']
    assert len(m.get('/api/merchant/conversations').json()['reviews']) == 1
    assert m.get('/api/merchant/conversations').json()['pending'] == []
    with review_db() as db:
        assert ai_review.context(db, db.get(Conversation, cid)).clarification_rounds == 0


def test_handoff_proposal_approval_transfers_unresolved_request(review_db, monkeypatch):
    proposal = {'schema_version': 2, 'action': 'handoff',
        'needs': [{'subject': '售后申请', 'attribute': '退款', 'status': 'human_decision', 'claims': []}],
        'question': None, 'reason': '退款申请需要商家处理。'}
    monkeypatch.setattr(ai_review, 'generate', lambda *args: {
        'status': 'complete', 'finish_reason': 'stop', 'content': json.dumps(proposal)})
    v, cid = visitor()
    m = merchant()
    v.post('/api/visitor/messages', json=payload('退款'))
    assert decide(m, cid, draft_for(m, cid), content='退款申请已转交人工核实。').status_code == 200
    assert v.get('/api/visitor/conversation').json()['status'] == 'waiting_human'
    assert len(m.get('/api/merchant/conversations').json()['pending']) == 1


def test_paid_mode_without_approval_never_calls_provider(review_db, monkeypatch):
    monkeypatch.setattr(config, 'AI_REVIEW_MODE', 'deepseek')
    monkeypatch.setattr(ai_review, 'retrieve', lambda *args: [])
    from app import deepseek_probe
    monkeypatch.setattr(deepseek_probe, 'send_once', lambda *args: pytest.fail('Unexpected paid request'))
    v, cid = visitor()
    v.post('/api/visitor/messages', json=payload('问题'))
    assert draft_for(merchant(), cid)['status'] == 'failed'


def test_restart_fails_interrupted_job_without_retry(review_db, monkeypatch):
    monkeypatch.setattr(ai_review, 'run_review', lambda *args: None)
    v, cid = visitor()
    v.post('/api/visitor/messages', json=payload('中断任务'))
    ai_review.recover_interrupted()
    draft = draft_for(merchant(), cid)
    assert draft['status'] == 'failed' and '重启' in draft['note']
    assert decide(merchant(), cid, draft, 'handoff').status_code == 200


def test_limit_reached_is_explicit_and_can_transfer(review_db, monkeypatch):
    def limited(*args):
        raise ai_review.TrialLimitError('已达到本地费用预留上限，这不是接口故障。')
    monkeypatch.setattr(ai_review, 'generate', limited)
    v, cid = visitor()
    m = merchant()
    v.post('/api/visitor/messages', json=payload('问题'))
    draft = draft_for(m, cid)
    assert draft['status'] == 'limited' and '费用预留上限' in draft['note']
    assert v.get('/api/visitor/conversation').json()['ai']['phase'] == 'limited'
    assert decide(m, cid, draft).status_code == 409
    assert decide(m, cid, draft, 'handoff').status_code == 200


def test_cited_proposal_only_available_to_authenticated_reviewer(review_db, monkeypatch):
    from app.retrieval import load_documents
    _, docs = load_documents(ai_review.ROOT / 'knowledge/public.json')
    doc = next(item for item in docs if item['id'] == 'K11')
    monkeypatch.setattr(config, 'AI_REVIEW_MODE', 'deepseek')
    monkeypatch.setattr(ai_review, 'retrieve', lambda *args: [doc | {'score': 99, 'components': {'internal': 1}}])
    proposal = {'schema_version': 2, 'action': 'answer', 'needs': [{
        'subject': '二团商品', 'attribute': '价格', 'status': 'supported', 'claims': [{
        'kind': 'fact', 'subject': '二团商品', 'attribute': '价格', 'text': doc['facts'],
        'evidence': [{'knowledge_id': 'K11', 'field': 'facts', 'quote': doc['facts']}]}]}],
        'question': None, 'reason': None}
    monkeypatch.setattr(ai_review, 'generate', lambda *args: {
        'status': 'complete', 'finish_reason': 'stop', 'content': json.dumps(proposal)})
    v, cid = visitor()
    m = merchant()
    v.post('/api/visitor/messages', json=payload('二团价格'))
    draft = draft_for(m, cid)
    assert draft['status'] == 'ready' and draft['candidate'] == doc['facts']
    assert draft['evidence'][0]['sources'] == doc['sources']
    assert 'score' not in draft['evidence'][0] and 'components' not in draft['evidence'][0]
    public = v.get('/api/visitor/conversation').json()
    assert len(public['messages']) == 1 and 'evidence' not in json.dumps(public)
    assert decide(m, cid, draft, content=doc['facts']).status_code == 200
    assert v.get('/api/visitor/conversation').json()['messages'][-1]['content'] == doc['facts']
