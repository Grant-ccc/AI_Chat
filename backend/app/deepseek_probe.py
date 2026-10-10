"""Explicit offline-test HTTP adapter; no chat database or automatic retries."""
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter

import httpx
from dotenv import dotenv_values

PRICE_DATE = '2026-10-10'
PRICE_SOURCE = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
INPUT_RATE = Decimal('2')  # Peak, all inputs treated as cache misses, CNY / 1M.
OUTPUT_RATE = Decimal('8')


@dataclass(frozen=True)
class ProbeSettings:
    api_key: str = field(repr=False)
    base_url: str = 'https://api.deepseek.com'
    model: str = 'deepseek-flash'
    timeout: int = 90
    max_calls: int = 7
    budget: Decimal = Decimal('1.00')

    @classmethod
    def load(cls, path):
        values = dict(dotenv_values(path))
        for key in values:
            if key.startswith('DEEPSEEK_') and key in os.environ:
                values[key] = os.environ[key]
        result = cls(api_key=values.get('DEEPSEEK_API_KEY') or '',
                     base_url=values.get('DEEPSEEK_BASE_URL') or '',
                     model=values.get('DEEPSEEK_MODEL') or '',
                     timeout=int(values.get('DEEPSEEK_TIMEOUT_SECONDS') or '90'),
                     max_calls=int(values.get('DEEPSEEK_TEST_MAX_CALLS') or '7'),
                     budget=Decimal(values.get('DEEPSEEK_TEST_BUDGET_CNY') or '1.00'))
        result.validate()
        return result

    def validate(self):
        if not self.api_key.strip() or any(c.isspace() for c in self.api_key):
            raise ValueError('请检查本地Key是否已填写且没有空白字符')
        if self.base_url != 'https://api.deepseek.com' or self.model != 'deepseek-flash':
            raise ValueError('本轮仅授权DeepSeek官方地址和deepseek-flash模型')
        if not 1 <= self.timeout <= 90 or not 1 <= self.max_calls <= 7:
            raise ValueError('超出本轮超时或次数限制')
        if not self.budget.is_finite() or not Decimal('0') < self.budget <= Decimal('1'):
            raise ValueError('超出本轮预算限制')


def prepare_payload(request, settings):
    settings.validate()
    if request.get('model') != settings.model or request.get('stream') is not False:
        raise ValueError('请求必须使用本轮模型且关闭流式输出')
    if request.get('response_format') != {'type': 'json_object'}:
        raise ValueError('本轮要求JSON输出')
    payload = dict(request, max_tokens=2048, thinking={'type': 'disabled'})
    # Byte count plus framing allowance is a conservative estimate, not a tokenizer.
    # Reserve twice this estimate; also check reported tokens after the request.
    input_allowance = 2 * (len(json.dumps(payload['messages'], ensure_ascii=False).encode('utf-8')) + 4096)
    if input_allowance > 100000:
        raise ValueError('本轮请求过长')
    reserved = (Decimal(input_allowance) * INPUT_RATE + Decimal(payload['max_tokens']) * OUTPUT_RATE) / 1000000
    return payload, input_allowance, reserved


class CallLedger:
    """Atomic, persistent reservations. Failed/unknown calls retain their full reserve."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, case_id TEXT NOT NULL, '
                       'reserved TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL)')

    def snapshot(self):
        with sqlite3.connect(self.path) as db:
            rows = db.execute('SELECT reserved, status FROM attempts').fetchall()
        return dict(calls=len(rows), reserved_cny=str(sum((Decimal(row[0]) for row in rows), Decimal('0'))),
                    blocked=any(row[1] != 'complete' for row in rows))

    def reserve(self, case_id, cost, settings):
        settings.validate()
        if date.today().isoformat() != PRICE_DATE:
            raise ValueError('价格快照不是今天核实的版本，请先重新核实官方价格')
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT case_id, reserved, status FROM attempts').fetchall()
            if any(row[2] != 'complete' for row in rows):
                raise ValueError('存在失败或结果未知的调用，本轮暂停；不自动重试')
            if any(row[0] == case_id for row in rows):
                raise ValueError('此题已有调用记录，不重复发送')
            total = sum((Decimal(row[1]) for row in rows), Decimal('0'))
            if len(rows) >= settings.max_calls or total + cost > settings.budget:
                raise ValueError('已达到调用次数或费用预留上限')
            cursor = db.execute('INSERT INTO attempts(case_id,reserved,status,created) VALUES(?,?,?,?)',
                                (case_id, str(cost), 'pending', datetime.now(timezone.utc).isoformat()))
            return cursor.lastrowid

    def finish(self, attempt, status):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE attempts SET status=? WHERE id=? AND status=?', (status, attempt, 'pending'))


def send_once(request, settings, ledger, case_id, transport=None):
    """Exactly one POST; exceptions are reduced to safe categories, never response bodies."""
    payload, input_allowance, reserved = prepare_payload(request, settings)
    attempt = ledger.reserve(case_id, reserved, settings)
    started = perf_counter()
    result = dict(attempt=attempt, reserved_cny=str(reserved), price_source=PRICE_SOURCE,
                  price_verified_on=PRICE_DATE, thinking='disabled', max_tokens=payload['max_tokens'])
    status = 'failed'
    try:
        with httpx.Client(timeout=settings.timeout, follow_redirects=False, trust_env=False,
                          transport=transport) as client:
            response = client.post(settings.base_url + '/chat/completions', json=payload,
                                   headers={'Authorization': 'Bearer ' + settings.api_key})
        if response.status_code != 200:
            result.update(status='http_error', http_status=response.status_code)
        else:
            raw = response.json()
            choice = raw['choices'][0]
            content = choice['message']['content']
            finish = choice['finish_reason']
            usage = raw['usage']
            prompt, completion = usage['prompt_tokens'], usage['completion_tokens']
            if any(type(n) is not int or n < 0 for n in [prompt, completion]):
                raise ValueError('Invalid usage')
            if not isinstance(content, str) or not isinstance(finish, str):
                raise ValueError('Invalid output')
            if prompt > input_allowance or completion > payload['max_tokens']:
                result.update(status='usage_exceeded_reserve', prompt_tokens=prompt, completion_tokens=completion)
            else:
                # Store public generated content, not headers or raw server errors.
                result.update(status='complete', model=str(raw.get('model', '')).replace(settings.api_key, '[redacted]'),
                              content=content.replace(settings.api_key, '[redacted]'), finish_reason=finish,
                              usage=dict(prompt_tokens=prompt, completion_tokens=completion),
                              peak_cost_upper_estimate_cny=str((Decimal(prompt)*INPUT_RATE + Decimal(completion)*OUTPUT_RATE)/1000000))
                status = 'complete'
    except httpx.TimeoutException:
        result['status'] = 'timeout'
    except httpx.HTTPError:
        result['status'] = 'network_error'
    except (ValueError, TypeError, KeyError, IndexError):
        result['status'] = 'invalid_response'
    finally:
        ledger.finish(attempt, status)
    result['elapsed_seconds'] = round(perf_counter() - started, 3)
    return result
