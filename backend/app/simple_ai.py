"""完整公开prompt生成自然回复，可选择一个转人工动作，不使用检索。"""
from dataclasses import dataclass
import json
import re
import sqlite3
from threading import Lock
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values
from sqlalchemy import select, desc
from pydantic import BaseModel, ConfigDict, Field
from . import config
from .db import SessionLocal
from .models import AiReply, Message, now

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = ('queued', 'generating')
MODEL = 'deepseek-flash'
MAX_OUTPUT = 1024
CALL_LOCK = Lock()


class LimitError(Exception):
    pass


class TransferInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=200)
    reply: str = Field(min_length=1, max_length=2000)


@dataclass
class TransferReply:
    reply: str
    reason: str


TRANSFER_TOOL = {'type': 'function', 'function': {
    'name': 'transfer_to_human',
    'description': '用户主动要求人工、申请退款换货赔偿、责任判断、规则例外、需核实订单参数或两轮追问仍不清时转人工。一般规则、闲聊和UPF报告缺失不自动转交。',
    'parameters': {'type': 'object', 'properties': {
        'reason': {'type': 'string', 'description': '简短公开转交原因'},
        'reply': {'type': 'string', 'description': '完整自然回复，先回答可确认部分并提醒材料；不声称已转交或已批准'}},
        'required': ['reason', 'reply'], 'additionalProperties': False}}}


def parse_reply(choice, request):
    message = choice['message']
    if message.get('tool_calls'):
        calls = message['tool_calls']
        if (choice['finish_reason'] != 'tool_calls' or not request.get('tools') or len(calls) != 1
                or calls[0].get('type') != 'function'
                or calls[0]['function']['name'] != 'transfer_to_human'):
            raise ValueError('Unexpected tool action')
        args = TransferInput.model_validate_json(calls[0]['function']['arguments'])
        return TransferReply(args.reply, args.reason)
    text = message.get('content')
    if choice['finish_reason'] != 'stop' or not isinstance(text, str) or not text.strip() or len(text) > 6000:
        raise ValueError('Incomplete or oversized reply')
    if not request.get('response_format') and len(text) > 2000:
        raise ValueError('Reply too long')
    return text.strip()


def system_prompt():
    source = (ROOT / 'docs/客服prompt模板_共创草稿.md').read_text(encoding='utf-8')
    blocks = re.findall(r'```text\n(.*?)\n```', source, re.S)
    if len(blocks) != 2:
        raise ValueError('Prompt structure changed')
    # Validate the calendar without changing the approved instructions.
    datetime.strptime(config.AI_SIMULATION_DATE, '%Y-%m-%d')
    values = {'客服名称': '花窗伞客服', '业务范围': '第二轮团购历史模拟',
              '业务日期': config.AI_SIMULATION_DATE, '业务时区': 'Asia/Shanghai',
              '语气要求': '自然、简洁，像团购工作人员聊天',
              '闲聊方式': '简短自然回应，不主动介绍商品', '公开业务资料': blocks[1]}
    result = blocks[0]
    for key, value in values.items():
        result = result.replace('{{' + key + '}}', value)
    if '{{' in result:
        raise ValueError('Unresolved prompt field')
    return result


def latest(db, c):
    return db.scalar(select(AiReply).where(AiReply.conversation_id == c.id,
        AiReply.user_sequence == c.latest_user_sequence))


def public_status(db, c):
    task = latest(db, c)
    return {'enabled': config.AI_WEB_MODE != 'disabled',
            'simulation_date': config.AI_SIMULATION_DATE,
            'status': task.status if task and c.status == 'ai_ready' else 'idle'}


def invalidate(db, c):
    for task in db.scalars(select(AiReply).where(AiReply.conversation_id == c.id,
                                               AiReply.status.in_(ACTIVE)).with_for_update()):
        task.status = 'stale'


def enqueue(db, c):
    invalidate(db, c)
    if config.AI_WEB_MODE not in ('mock', 'deepseek') or c.status != 'ai_ready':
        return None
    task = AiReply(conversation_id=c.id, user_sequence=c.latest_user_sequence,
                   source_revision=c.revision)
    db.add(task)
    db.flush()
    return task.id


def round_messages(db, c):
    # Each completed human consultation ends at its persisted system event.
    cutoff = db.scalar(select(Message.sequence).where(Message.conversation_id == c.id,
        Message.role == 'system', Message.content.like('商家已结束本次处理。%'))
        .order_by(desc(Message.sequence)).limit(1)) or 0
    messages = list(db.scalars(select(Message).where(Message.conversation_id == c.id,
        Message.sequence > cutoff, Message.role.in_(('user', 'assistant')))
        .order_by(Message.sequence)))
    # Fail explicitly instead of silently dropping early conditions from the round.
    if len(messages) > 80:
        raise LimitError('Round too long')
    return [{'role': m.role, 'content': m.content} for m in messages]


def ledger_call(request, task_id, key):
    path = ROOT / '.local/simple-web/ledger.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    allowance = (len(json.dumps(request, ensure_ascii=False).encode()) + 1024) * 2
    if allowance > 100_000:
        raise LimitError('Input too long')
    reserved = (allowance * 2 + MAX_OUTPUT * 8) / 1_000_000
    with sqlite3.connect(path, timeout=10) as ledger:
        ledger.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, status TEXT NOT NULL, reserve REAL NOT NULL, input_tokens INTEGER, output_tokens INTEGER)')
        ledger.commit()
        ledger.execute('BEGIN IMMEDIATE')
        count, spent = ledger.execute('SELECT COUNT(*), COALESCE(SUM(reserve),0) FROM calls').fetchone()
        if (ledger.execute("SELECT 1 FROM calls WHERE status != 'complete'").fetchone()
                or count >= config.AI_WEB_MAX_CALLS or spent + reserved > config.AI_WEB_BUDGET_CNY):
            raise LimitError('Budget, count or uncertain request guard')
        ledger.execute('INSERT INTO calls (id,status,reserve) VALUES (?,?,?)', (task_id, 'pending', reserved))
        ledger.commit()
        try:
            with httpx.Client(timeout=45, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)) as client:
                response = client.post('https://api.deepseek.com/chat/completions', json=request,
                                       headers={'Authorization': 'Bearer ' + key})
            response.raise_for_status()
            data = response.json()
            choice, usage = data['choices'][0], data['usage']
            text = parse_reply(choice, request)
            if (usage['prompt_tokens'] > allowance
                    or usage['completion_tokens'] > MAX_OUTPUT):
                raise ValueError('Incomplete or oversized reply')
            ledger.execute("UPDATE calls SET status='complete',input_tokens=?,output_tokens=? WHERE id=?",
                           (usage['prompt_tokens'], usage['completion_tokens'], task_id))
            ledger.commit()
            return text
        except Exception:
            ledger.execute("UPDATE calls SET status='failed_or_unknown' WHERE id=?", (task_id,))
            ledger.commit()
            raise


def generate(messages, task_id):
    if config.AI_WEB_MODE == 'mock':
        if any(word in messages[-1]['content'] for word in ('想换一把', '给我换一把', '申请退款')):
            return TransferReply('【模拟 AI 回复】该售后申请需要商家判断；请准备订单截图与瑕疵照片，缺材料也可以先转交。', '售后申请需要商家判断')
        return '【模拟 AI 回复】已收到你的问题；这条回复仅用于验证网页流程，需要人工时请点击“转人工”。'
    return call_model(system_prompt(), messages, task_id, tools=[TRANSFER_TOOL])


def call_model(system, messages, task_id, **options):
    if config.AI_WEB_MODE != 'deepseek':
        raise ValueError('AI disabled')
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    if config.AI_PRICE_DATE != today:
        raise LimitError('Price snapshot expired')
    key = dotenv_values(ROOT / 'backend/.env').get('DEEPSEEK_API_KEY')
    if not key or key != key.strip():
        raise ValueError('Missing local key')
    request = {'model': MODEL, 'thinking': {'type': 'disabled'}, 'stream': False,
               'temperature': 0.2, 'max_tokens': MAX_OUTPUT,
               'messages': [{'role': 'system', 'content': system}, *messages], **options}
    return ledger_call(request, task_id, key)


def run(task_id):
    from .conversations import conversation, add_message
    try:
        with SessionLocal() as db:
            task = db.get(AiReply, task_id)
            if not task:
                return
            c = conversation(db, task.conversation_id, lock=True)
            db.refresh(task)
            if task.status != 'queued':
                return
            if c.status != 'ai_ready' or c.revision != task.source_revision:
                task.status = 'stale'
                db.commit()
                return
            task.status = 'generating'
            c.revision += 1
            task.source_revision = c.revision
            messages = round_messages(db, c)
            started = task.source_revision
            db.commit()
        # Single-process development service: serialize paid calls without blocking message writes.
        with CALL_LOCK:
            with SessionLocal() as db:
                task = db.get(AiReply, task_id)
                c = conversation(db, task.conversation_id, lock=True)
                db.refresh(task)
                if task.status != 'generating' or c.status != 'ai_ready' or c.revision != started:
                    task.status = 'stale'
                    db.commit()
                    return
            content = generate(messages, task_id)
        status = 'complete'
    except LimitError:
        status, content = 'limited', None
    except Exception:
        # No provider body, credentials or internal prompt in user errors/logs.
        status, content = 'failed', None
    summary_id = None
    with SessionLocal() as db:
        task = db.get(AiReply, task_id)
        if not task:
            return
        c = conversation(db, task.conversation_id, lock=True)
        db.refresh(task)
        if (task.status not in ACTIVE or c.status != 'ai_ready'
                or c.latest_user_sequence != task.user_sequence
                or (content is not None and c.revision != started)):
            task.status = 'stale'
        else:
            task.status = status
            if content is not None:
                if c.sequence >= 2000:
                    task.status = 'limited'
                else:
                    transfer = content if isinstance(content, TransferReply) else None
                    add_message(db, c, 'assistant', transfer.reply if transfer else content)
                    if transfer:
                        from .conversations import open_handoff, latest_handoff
                        h = open_handoff(db, c, latest_handoff(db, c, lock=True), transfer.reason,
                                         '已转人工：' + transfer.reason + '。可以在这里继续补充文字。')
                        summary_id = h.id
            c.revision += 1
            c.updated_at = now()
        db.commit()
    if summary_id:
        from .handoff_summary import run_summary
        run_summary(summary_id)


def recover():
    with SessionLocal() as db:
        ids = list(db.scalars(select(AiReply.id).where(AiReply.status.in_(ACTIVE))))
    from .conversations import conversation
    for task_id in ids:
        with SessionLocal() as db:
            task = db.get(AiReply, task_id)
            c = conversation(db, task.conversation_id, lock=True)
            db.refresh(task)
            if task.status in ACTIVE:
                task.status = 'failed'
                c.revision += 1
                db.commit()
    from .handoff_summary import recover_summaries
    recover_summaries()
