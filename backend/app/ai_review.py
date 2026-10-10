"""候选生成后台任务。会话锁只用于落库，生成期间不持有锁；绝不自动发布。"""
import json
from functools import lru_cache
from pathlib import Path
from sqlalchemy import select, desc
from . import config
from .db import SessionLocal
from .models import AiReview, Message, now
from .answer_contract import GenerationContext, build_deepseek_request, check_proposal, public_evidence

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = ('queued', 'generating', 'ready', 'failed', 'invalid', 'rejected')


def current_review(db, c):
    return db.scalar(select(AiReview).where(AiReview.conversation_id == c.id,
        AiReview.user_sequence == c.latest_user_sequence))


def invalidate_reviews(db, c):
    for draft in db.scalars(select(AiReview).where(AiReview.conversation_id == c.id,
            AiReview.status.in_(ACTIVE)).with_for_update()):
        draft.status = 'stale'
        draft.note = '会话已更新，候选已作废。'


def enqueue(db, c):
    invalidate_reviews(db, c)
    if c.status != 'ai_ready' or config.AI_REVIEW_MODE not in ('mock', 'deepseek'):
        return None
    draft = AiReview(conversation_id=c.id, user_sequence=c.latest_user_sequence,
                     source_revision=c.revision, mode=config.AI_REVIEW_MODE)
    db.add(draft)
    db.flush()
    return draft.id


def round_start(db, c):
    from .conversations import latest_handoff
    h = latest_handoff(db, c)
    if not h or not h.ended_at:
        return 0
    if h.ended_sequence is not None:
        return h.ended_sequence
    # Compatibility with historical rows; use the persisted end event, not second-resolution dates.
    return db.scalar(select(Message.sequence).where(Message.conversation_id == c.id,
        Message.role == 'system', Message.content.like('商家已结束本次处理。%'),
        Message.sequence > h.covered_sequence).order_by(desc(Message.sequence)).limit(1)) or 0


def context(db, c):
    # Only consecutive approved clarification turns in the current consultation count.
    cutoff = round_start(db, c)
    turns = 0
    for draft in db.scalars(select(AiReview).where(AiReview.conversation_id == c.id,
            AiReview.status == 'approved').order_by(desc(AiReview.user_sequence))):
        if draft.user_sequence <= cutoff:
            break
        if not draft.proposal_json or json.loads(draft.proposal_json)['action'] != 'clarify':
            break
        turns += 1
        if turns == 2:
            break
    return GenerationContext(status=c.status, revision=c.revision,
        latest_user_sequence=c.latest_user_sequence, business_round=config.AI_BUSINESS_ROUND,
        simulation_date=config.AI_SIMULATION_DATE, clarification_rounds=turns)


@lru_cache(maxsize=1)
def public_documents():
    from .retrieval import load_documents
    _, docs = load_documents(ROOT / 'knowledge/public.json')
    return docs


def retrieve(question, history, started):
    # Small MVP corpus: provide all applicable public facts instead of losing short questions
    # in top-k ranking. History stays separate in the generation request. Input/budget guards
    # still reject oversized requests; never silently truncate facts to make them fit.
    return [doc for doc in public_documents() if doc['round'] == started.business_round]


def generate(draft_id, mode, started, question, history, hits):
    if mode == 'mock':
        return {'status': 'complete', 'finish_reason': 'stop', 'content': json.dumps({
            'schema_version': 2, 'action': 'insufficient',
            'needs': [{'subject': '当前咨询', 'attribute': '业务回答',
                       'status': 'missing_knowledge', 'claims': []}],
            'question': None,
            'reason': '这是模拟联调候选，用于验证审核流程；真实业务回答需要启用官方模型。'}, ensure_ascii=False)}
    if mode != 'deepseek' or not config.AI_REVIEW_PAID_APPROVED:
        return {'status': 'disabled'}
    from .deepseek_probe import ProbeSettings, CallLedger, send_once
    settings = ProbeSettings.load(ROOT / 'backend/.env')
    policy = json.loads((ROOT / 'knowledge/answer-policy.json').read_text(encoding='utf-8'))
    request = build_deepseek_request(started, question, history, hits, policy, settings.model)
    # A separate, persistent web trial budget. Never clear or reuse the spent offline ledger.
    ledger = CallLedger(ROOT / '.local/ai-review/web-trial-seven-calls.sqlite3')
    return send_once(request, settings, ledger, 'review-' + draft_id)


def render(proposal):
    texts = [claim['text'].strip() for need in proposal['needs'] for claim in need['claims']]
    texts += [value.strip() for value in [proposal['question'], proposal['reason']] if value and value.strip()]
    return '\n'.join(dict.fromkeys(texts))


def bump(c):
    c.revision += 1
    c.updated_at = now()


def run_review(draft_id):
    from .conversations import conversation
    with SessionLocal() as db:
        initial = db.get(AiReview, draft_id)
        if not initial:
            return
        c = conversation(db, initial.conversation_id, lock=True)
        draft = db.scalar(select(AiReview).where(AiReview.id == draft_id).with_for_update().execution_options(populate_existing=True))
        if draft.status != 'queued':
            return
        if c.status != 'ai_ready' or c.revision != draft.source_revision or c.latest_user_sequence != draft.user_sequence:
            draft.status = 'stale'
            db.commit()
            return
        draft.status = 'generating'
        bump(c)
        draft.source_revision = c.revision
        started = context(db, c)
        cutoff = round_start(db, c)
        messages = list(db.scalars(select(Message).where(Message.conversation_id == c.id,
            Message.role != 'system').order_by(desc(Message.sequence)).limit(21)))[::-1]
        messages = [m for m in messages if m.sequence > cutoff]
        question = next(m.content for m in messages if m.sequence == draft.user_sequence)
        history = [{'role': m.role, 'content': m.content} for m in messages if m.sequence < draft.user_sequence]
        mode = draft.mode
        db.commit()
    try:
        hits = [] if mode == 'mock' else retrieve(question, history, started)
        result = generate(draft_id, mode, started, question, history, hits)
    except Exception:
        # No provider error body, secret, raw output or traceback in visitor-facing data.
        hits, result = [], {'status': 'failed'}
    with SessionLocal() as db:
        c = conversation(db, initial.conversation_id, lock=True)
        draft = db.scalar(select(AiReview).where(AiReview.id == draft_id).with_for_update())
        current = context(db, c)
        if draft.status != 'generating' or current != started:
            if draft.status == 'generating':
                draft.status = 'stale'
                db.commit()
            return
        if result.get('status') != 'complete':
            draft.status, draft.note = 'failed', '未能生成可用候选，请转人工处理；不会自动重试。'
        else:
            checked = check_proposal(result.get('content'), hits, started, current, result.get('finish_reason'))
            if checked['status'] != 'citation_checked_pending_review':
                draft.status, draft.note = 'invalid', '候选未通过结构或引用检查，请转人工处理。'
            else:
                proposal = checked['proposal']
                text = render(proposal)
                if not text.strip() or len(text) > 2000:
                    draft.status, draft.note = 'invalid', '候选内容长度不符合要求，请转人工处理。'
                else:
                    draft.status, draft.note = 'ready', checked['reason']
                    draft.proposal_json = json.dumps(proposal, ensure_ascii=False)
                    draft.evidence_json = json.dumps(list(public_evidence(hits, started).values()), ensure_ascii=False)
                    draft.candidate = text
        bump(c)
        draft.source_revision = c.revision
        db.commit()


def recover_interrupted():
    """Single-process MVP: interrupted jobs are failed on restart, never billed again."""
    from .conversations import conversation
    with SessionLocal() as db:
        ids = list(db.scalars(select(AiReview.id).where(AiReview.status.in_(('queued', 'generating')))))
    for identity in ids:
        with SessionLocal() as db:
            initial = db.get(AiReview, identity)
            c = conversation(db, initial.conversation_id, lock=True)
            draft = db.scalar(select(AiReview).where(AiReview.id == identity).with_for_update())
            if draft.status in ('queued', 'generating'):
                draft.status, draft.note = 'failed', '生成任务被服务重启中断，请转人工处理。'
                bump(c)
                draft.source_revision = c.revision
                db.commit()


def review_data(draft):
    return dict(id=draft.id, status=draft.status, mode=draft.mode, user_sequence=draft.user_sequence,
        candidate=draft.candidate, note=draft.note,
        proposal=json.loads(draft.proposal_json) if draft.proposal_json else None,
        evidence=json.loads(draft.evidence_json) if draft.evidence_json else [],
        final_content=draft.final_content, reviewed_by=draft.reviewed_by)
