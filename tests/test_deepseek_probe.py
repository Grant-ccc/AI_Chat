"""HTTP adapter and persistent budget checks using mock transport, never real keys/API."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.deepseek_probe import CallLedger, ProbeSettings, WebSettings, TrialLimitError, prepare_payload, send_once


def request():
    return dict(model='deepseek-flash', messages=[dict(role='user', content='只输出json')],
                stream=False, response_format={'type': 'json_object'}, max_tokens=4096)


@pytest.fixture(autouse=True)
def verified_today(monkeypatch):
    from datetime import date
    monkeypatch.setattr('app.deepseek_probe.PRICE_DATE', date.today().isoformat())


def settings(**kwargs):
    return ProbeSettings(**(dict(api_key='fake-test-secret') | kwargs))


def test_key_not_in_settings_repr():
    assert 'fake-test-secret' not in repr(settings())


@pytest.mark.parametrize('change', [dict(api_key=''), dict(base_url='https://example.com'),
    dict(base_url='https://api.deepseek.com@evil.example'), dict(model='deepseek-v4-pro'),
    dict(max_calls=8), dict(budget=Decimal('1.01')), dict(budget=Decimal('NaN')), dict(timeout=91)])
def test_unapproved_configuration_rejected(change):
    with pytest.raises(ValueError):
        settings(**change).validate()


def test_preparation_limits_output_and_disables_thinking():
    payload, allowance, reserve = prepare_payload(request(), settings())
    assert payload['thinking'] == {'type': 'disabled'}
    assert payload['max_tokens'] == 2048
    assert allowance > len(json.dumps(payload['messages']).encode())
    assert Decimal('0') < reserve < Decimal('1')


def test_persistent_count_budget_duplicate_and_failed_guards(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    ledger = CallLedger(path)
    first = ledger.reserve('one', Decimal('.6'), settings())
    with pytest.raises(ValueError):
        ledger.reserve('two', Decimal('.1'), settings())  # Pending excludes simultaneous calls.
    ledger.finish(first, 'complete')
    ledger = CallLedger(path)
    with pytest.raises(ValueError):
        ledger.reserve('one', Decimal('.1'), settings())
    with pytest.raises(ValueError):
        ledger.reserve('two', Decimal('.5'), settings())
    second = ledger.reserve('two', Decimal('.1'), settings())
    ledger.finish(second, 'failed')
    with pytest.raises(ValueError):
        ledger.reserve('three', Decimal('.1'), settings())
    assert ledger.snapshot() == dict(calls=2, reserved_cny='0.7', blocked=True)


def test_seventh_call_survives_process_restart(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    for index in range(7):
        ledger = CallLedger(path)
        attempt = ledger.reserve(str(index), Decimal('.01'), settings())
        ledger.finish(attempt, 'complete')
    with pytest.raises(ValueError):
        CallLedger(path).reserve('eighth', Decimal('.01'), settings())


def test_web_unlimited_keeps_previous_records_and_budget_guard(tmp_path):
    path = tmp_path / 'web.sqlite3'
    for index in range(7):
        ledger = CallLedger(path)
        attempt = ledger.reserve(str(index), Decimal('.01'), settings())
        ledger.finish(attempt, 'complete')
    web = WebSettings(api_key='fake-test-secret')
    ledger = CallLedger(path)
    attempt = ledger.reserve('eighth', Decimal('.01'), web)
    ledger.finish(attempt, 'complete')
    assert ledger.snapshot()['calls'] == 8
    assert ledger.snapshot()['reserved_cny'] == '0.08'
    with pytest.raises(TrialLimitError, match='费用预留上限'):
        ledger.reserve('too-expensive', Decimal('1'), web)
    assert ledger.snapshot()['calls'] == 8


def test_web_configuration_is_separate_from_offline(tmp_path):
    path = tmp_path / 'config.env'
    path.write_text('DEEPSEEK_API_KEY=fake-test-secret\nDEEPSEEK_BASE_URL=https://api.deepseek.com\n'
                    'DEEPSEEK_MODEL=deepseek-flash\nDEEPSEEK_TEST_MAX_CALLS=7\n'
                    'DEEPSEEK_TEST_BUDGET_CNY=1.00\nDEEPSEEK_WEB_MAX_CALLS=0\n'
                    'DEEPSEEK_WEB_BUDGET_CNY=1.00\n', encoding='utf-8')
    assert ProbeSettings.load(path).max_calls == 7
    assert WebSettings.load(path).max_calls == 0
    assert WebSettings.load(path).budget == Decimal('1')


def test_web_finite_call_limit_and_failed_guard_still_work(tmp_path):
    ledger = CallLedger(tmp_path / 'finite.sqlite3')
    web = WebSettings(api_key='fake-test-secret', max_calls=1)
    attempt = ledger.reserve('one', Decimal('.01'), web)
    ledger.finish(attempt, 'complete')
    with pytest.raises(TrialLimitError, match='调用次数上限'):
        ledger.reserve('two', Decimal('.01'), web)
    unlimited = WebSettings(api_key='fake-test-secret')
    attempt = ledger.reserve('two', Decimal('.01'), unlimited)
    ledger.finish(attempt, 'failed')
    with pytest.raises(ValueError, match='失败或结果未知'):
        ledger.reserve('three', Decimal('.01'), unlimited)


@pytest.mark.parametrize('change', [dict(max_calls=-1), dict(budget=Decimal('0')),
                                  dict(budget=Decimal('NaN')), dict(timeout=91)])
def test_web_invalid_configuration_rejected(change):
    with pytest.raises(ValueError):
        WebSettings(api_key='fake-test-secret', **change).validate()


def test_concurrent_reservations_only_allow_one(tmp_path):
    ledger = CallLedger(tmp_path / 'ledger.sqlite3')
    def reserve(index):
        try:
            ledger.reserve(str(index), Decimal('.1'), settings())
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reserve, [1, 2])) == 1


def test_stale_price_stops_before_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr('app.deepseek_probe.PRICE_DATE', '2000-01-01')
    ledger = CallLedger(tmp_path / 'ledger.sqlite3')
    with pytest.raises(ValueError):
        ledger.reserve('one', Decimal('.1'), settings())
    assert ledger.snapshot()['calls'] == 0


@pytest.mark.parametrize('kind', ['success', 'timeout', 'redirect', 'http_error', 'bad_json', 'over_usage'])
def test_transport_once_and_no_secret_in_saved_result(tmp_path, kind):
    seen = []
    def handler(req):
        seen.append(req)
        assert req.url == 'https://api.deepseek.com/chat/completions'
        assert req.headers['authorization'] == 'Bearer fake-test-secret'
        if kind == 'timeout':
            raise httpx.ReadTimeout('fake-test-secret', request=req)
        if kind == 'redirect':
            return httpx.Response(307, headers={'location': 'https://evil.example'})
        if kind == 'http_error':
            return httpx.Response(401, text='fake-test-secret')
        if kind == 'bad_json':
            return httpx.Response(200, text='fake-test-secret')
        return httpx.Response(200, json=dict(model='deepseek-flash',
            choices=[dict(message=dict(content='{"note":"fake-test-secret"}'), finish_reason='stop')],
            usage=dict(prompt_tokens=1000000 if kind == 'over_usage' else 10, completion_tokens=4)))
    ledger = CallLedger(tmp_path / 'ledger.sqlite3')
    result = send_once(request(), settings(), ledger, 'one', httpx.MockTransport(handler))
    assert len(seen) == 1
    assert 'fake-test-secret' not in json.dumps(result)
    assert ledger.snapshot()['calls'] == 1
    assert ledger.snapshot()['blocked'] is (kind != 'success')
    if kind == 'success':
        assert result['status'] == 'complete'
        assert Decimal(result['peak_cost_upper_estimate_cny']) < Decimal(result['reserved_cny'])
