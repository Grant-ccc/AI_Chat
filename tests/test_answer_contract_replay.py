"""Old output conversion must not silently repair invalid associations."""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from answer_contract_replay import nest_valid_legacy


def legacy():
    return dict(action='answer', needs=[dict(subject='商品', attribute='参数', status='supported', claim_indexes=[0])],
                claims=[dict(kind='fact', subject='商品', attribute='参数', text='示例事实',
                             evidence=[dict(knowledge_id='K01', field='facts', quote='示例事实')])],
                question=None, reason=None)


def test_valid_legacy_keeps_text_action_status_and_evidence_without_mutation():
    original = legacy()
    before = copy.deepcopy(original)
    converted = nest_valid_legacy(original)
    assert converted['needs'][0]['claims'] == original['claims']
    assert converted['action'] == original['action']
    assert converted['needs'][0]['status'] == original['needs'][0]['status']
    assert original == before
    assert 'claims' not in converted
    assert 'claim_indexes' not in converted['needs'][0]


@pytest.mark.parametrize('problem', ['orphan', 'duplicate', 'out_of_range', 'unresolved', 'extra_field'])
def test_invalid_legacy_is_not_repaired(problem):
    value = legacy()
    if problem == 'orphan':
        value['claims'].append(copy.deepcopy(value['claims'][0]))
    elif problem == 'duplicate':
        value['needs'][0]['claim_indexes'] = [0, 0]
    elif problem == 'out_of_range':
        value['needs'][0]['claim_indexes'] = [8]
    elif problem == 'unresolved':
        value['needs'][0]['status'] = 'human_decision'
    else:
        value['extra_field'] = 'unexpected'
    with pytest.raises(ValueError):
        nest_valid_legacy(value)
