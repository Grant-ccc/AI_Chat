"""Batch safety: no live API or database needed."""
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from deepseek_six_cases import CASES, preflight, run_cases
from app.deepseek_probe import ProbeSettings


class Ledger:
    def __init__(self, calls=1, cost='.06', blocked=False):
        self.state = dict(calls=calls, reserved_cny=cost, blocked=blocked)

    def snapshot(self):
        return self.state


def rows():
    return [dict(case_id=case['id'], category=case['category'], request={}, reserved_cny='.06') for case in CASES]


@pytest.mark.parametrize('ledger', [Ledger(calls=2), Ledger(cost='.9'), Ledger(blocked=True)])
def test_entire_group_requires_available_count_budget_and_clean_state(ledger):
    with pytest.raises(ValueError):
        preflight(rows(), ledger, ProbeSettings(api_key='mock'))


def test_group_with_first_call_only_fits_authorization():
    before, total = preflight(rows(), Ledger(), ProbeSettings(api_key='mock'))
    assert before['calls'] == 1
    assert total == Decimal('.42')
    assert len({row['id'] for row in CASES}) == 6


def test_network_failure_saves_and_stops_without_retry():
    sent, saved = [], []
    batch = rows()
    def sender(request, settings, ledger, case_id):
        sent.append(case_id)
        return dict(status='timeout')
    run_cases(batch, ProbeSettings(api_key='mock'), Ledger(), lambda: saved.append(True), sender)
    assert sent == [CASES[0]['id']]
    assert len(saved) == 1
    assert 'response' not in batch[1]


def test_invalid_model_structure_preserved_for_all_six_cases():
    batch = rows()
    for row in batch:
        row.update(context=dict(status='ai_ready', revision=0, latest_user_sequence=1,
                                business_round='二团', simulation_date='2026-05-04'), retrieval=dict(hits=[]))
    sent, saved = [], []
    def sender(request, settings, ledger, case_id):
        sent.append(case_id)
        return dict(status='complete', content='{}', finish_reason='stop')
    run_cases(batch, ProbeSettings(api_key='mock'), Ledger(), lambda: saved.append(True), sender)
    assert len(sent) == len(saved) == 6
    assert all(row['validation']['status'] == 'rejected' for row in batch)
    assert all(row['validation']['deliverable'] is False for row in batch)
