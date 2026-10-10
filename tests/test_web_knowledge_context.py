"""网页短问与模拟日期回归：检查真实请求输入，不连接数据库或调用模型。"""
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.ai_review import retrieve
from app.answer_contract import GenerationContext, build_deepseek_request


def context(day='2026-05-04', round_='二团'):
    return GenerationContext(status='ai_ready', revision=1, latest_user_sequence=1,
                             business_round=round_, simulation_date=day)


@pytest.mark.parametrize('question', ['明天还可以买的吗？', '多少钱？', '怎么收起来？'])
def test_short_questions_have_all_applicable_public_knowledge(question):
    hits = retrieve(question, [], context())
    assert len(hits) == 28 and len({h['id'] for h in hits}) == 28
    assert {'K14', 'K11', 'K12'} <= {h['id'] for h in hits}
    assert all(h['visibility'] == 'public' and h['round'] == '二团' for h in hits)
    assert not any(h['id'].startswith(('M', 'R', 'T')) for h in hits)


@pytest.mark.parametrize('day,tomorrow', [('2026-05-04', '2026-05-05'),
                                       ('2026-05-05', '2026-05-06'), ('2026-12-31', '2027-01-01')])
def test_request_has_simulated_calendar_and_actual_sale_evidence(day, tomorrow):
    ctx = context(day)
    policy = json.loads((ROOT / 'knowledge/answer-policy.json').read_text(encoding='utf-8'))
    request = build_deepseek_request(ctx, '明天还可以买的吗？', [], retrieve('明天还可以买的吗？', [], ctx), policy, 'deepseek-flash')
    data = json.loads(request['messages'][1]['content'])
    assert data['context']['simulation_date'] == day
    assert data['simulation_calendar']['today'] == day
    assert data['simulation_calendar']['tomorrow'] == tomorrow
    sale = next(doc for doc in data['candidates'] if doc['id'] == 'K14')
    assert '2026-05-05 23:59' in sale['facts']
    assert '不使用服务器现实日期' in request['messages'][0]['content']
    assert all('score' not in doc and 'search_text' not in doc for doc in data['candidates'])


def test_other_round_does_not_get_second_round_facts():
    assert retrieve('明天还能买吗？', [], context(round_='其他团购')) == []
