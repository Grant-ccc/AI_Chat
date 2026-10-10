import json
import pytest
from sqlalchemy import select, func
from app import config, simple_ai, handoff_summary as hs
from app.models import Handoff, HandoffSummary
from app.models import Conversation
from app.conversations import open_handoff, add_message
from test_handoff import visitor, merchant, payload


def detail(m, cid):
    return m.get(f'/api/merchant/conversations/{cid}').json()


def test_explicit_request_once_and_private(database, monkeypatch):
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'mock')
    monkeypatch.setattr(simple_ai, 'generate', lambda *_: pytest.fail('Direct request must bypass AI'))
    v, cid = visitor()
    message = payload('请转人工，谢谢')
    v.post('/api/visitor/messages', json=message)
    v.post('/api/visitor/messages', json=message)
    v.post('/api/visitor/handoff')
    public = v.get('/api/visitor/conversation').json()
    assert public['status'] == 'waiting_human' and 'handoff' not in public
    h = detail(merchant(), cid)['handoff']
    assert h['summary_status'] == 'ready'
    assert h['summary']['request'][0]['quote'] == '请转人工，谢谢'
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Handoff)) == 1
        assert db.scalar(select(func.count()).select_from(HandoffSummary)) == 1


def test_tool_transfer_answer_summary_and_supplement(database, monkeypatch):
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'mock')
    calls = []
    monkeypatch.setattr(simple_ai, 'generate', lambda *args: calls.append(args) or simple_ai.TransferReply('单面45元；换货需要商家处理。', '用户申请换货'))
    v, cid = visitor()
    v.post('/api/visitor/messages', json=payload('单面多少钱？我想换一把'))
    m = merchant()
    before = detail(m, cid)['handoff']
    assert before['summary_status'] == 'ready'
    public = v.get('/api/visitor/conversation').json()
    assert public['status'] == 'waiting_human'
    assert any('45元' in msg['content'] for msg in public['messages'])
    v.post('/api/visitor/messages', json=payload('转交后补充订单信息'))
    after = detail(m, cid)['handoff']
    assert after['summary'] == before['summary']
    assert after['summary_covered_sequence'] == before['summary_covered_sequence']
    assert len(calls) == 1
    root = f'/api/merchant/conversations/{cid}'
    m.post(root + '/takeover', json={'handoff_id': after['id']})
    m.post(root + '/end', json={'handoff_id': after['id']})
    v.post('/api/visitor/messages', json=payload('请转人工'))
    new = detail(m, cid)['handoff']
    assert new['round'] == 2 and new['summary_start_sequence'] > before['summary_covered_sequence']
    assert '想换一把' not in json.dumps(new['summary'], ensure_ascii=False)


@pytest.mark.parametrize('error,status', [(RuntimeError('private'), 'failed'), (simple_ai.LimitError(), 'limited')])
def test_summary_failure_does_not_block_takeover(database, monkeypatch, error, status):
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'mock')
    def fail(*_):
        raise error
    monkeypatch.setattr(hs, 'generate_summary', fail)
    v, cid = visitor()
    v.post('/api/visitor/handoff')
    m = merchant()
    h = detail(m, cid)['handoff']
    assert h['summary_status'] == status and h['summary'] is None
    assert m.post(f'/api/merchant/conversations/{cid}/takeover', json={'handoff_id': h['id']}).json()['status'] == 'human_active'


def test_empty_summary_has_no_model_call(monkeypatch):
    monkeypatch.setattr(simple_ai, 'call_model', lambda *_: pytest.fail('No model needed'))
    assert json.loads(hs.generate_summary([], 'empty')) == {k: [] for k in hs.FIELDS}


@pytest.mark.parametrize('text', ['不需要人工', '他说“转人工”', '退款规则是什么？', 'UPF报告在哪里', '单面多少钱'])
def test_ambiguous_text_does_not_bypass_model(text):
    assert not hs.explicit_human_request(text)


@pytest.mark.parametrize('sequence,quote', [(1, '虚构证据'), (2, '建议检查')])
def test_summary_requires_user_evidence(sequence, quote):
    body = {k: [] for k in hs.FIELDS}
    body['attempted'] = [{'text': '已检查', 'sequence': sequence, 'quote': quote}]
    with pytest.raises(ValueError):
        hs.validate_summary(json.dumps(body), [{'sequence': 1, 'role': 'user', 'content': '伞坏了'}, {'sequence': 2, 'role': 'assistant', 'content': '建议检查'}])


def test_transfer_parser_rejects_unknown_or_extra_actions():
    call = {'type': 'function', 'function': {'name': 'transfer_to_human', 'arguments': json.dumps({'reply': '请商家处理', 'reason': '换货'})}}
    choice = {'finish_reason': 'tool_calls', 'message': {'tool_calls': [call]}}
    assert isinstance(simple_ai.parse_reply(choice, {'tools': [simple_ai.TRANSFER_TOOL]}), simple_ai.TransferReply)
    with pytest.raises(ValueError):
        simple_ai.parse_reply(choice, {})
    choice['message']['tool_calls'].append(call)
    with pytest.raises(ValueError):
        simple_ai.parse_reply(choice, {'tools': [simple_ai.TRANSFER_TOOL]})


def test_late_transfer_does_not_duplicate_handoff(database, monkeypatch):
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'mock')
    v, cid = visitor()
    def answer(*_):
        with database() as db:
            c = db.get(Conversation, cid)
            open_handoff(db, c, None, '用户已请求人工', '已申请人工')
            db.commit()
        return simple_ai.TransferReply('迟到回复', '迟到交接')
    monkeypatch.setattr(simple_ai, 'generate', answer)
    v.post('/api/visitor/messages', json=payload('换货申请'))
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Handoff)) == 1
    assert all(m['content'] != '迟到回复' for m in v.get('/api/visitor/conversation').json()['messages'])


def test_late_summary_is_discarded_for_new_handoff(database, monkeypatch):
    monkeypatch.setattr(config, 'AI_WEB_MODE', 'mock')
    v, cid = visitor()
    old = []
    def generate(records, _):
        with database() as db:
            c = db.get(Conversation, cid)
            previous = db.scalar(select(Handoff).where(Handoff.conversation_id == cid))
            old.append(previous.id)
            c.status = 'ended'
            add_message(db, c, 'system', '商家已结束本次处理。')
            add_message(db, c, 'user', '新一轮问题')
            open_handoff(db, c, previous, '新交接', '再次转人工')
            db.commit()
        return json.dumps({k: [] for k in hs.FIELDS})
    monkeypatch.setattr(hs, 'generate_summary', generate)
    v.post('/api/visitor/handoff')
    with database() as db:
        assert db.get(HandoffSummary, old[0]).status == 'stale'
    h = detail(merchant(), cid)['handoff']
    assert h['round'] == 2 and h['summary'] is None
