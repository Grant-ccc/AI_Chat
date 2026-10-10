import json
from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict, field_validator
from sqlalchemy import select, desc, or_
from sqlalchemy.orm import Session
from .db import get_db
from .models import Conversation, Message, Handoff, AiReview, now
from .security import session_auth
from . import ai_review, config

router = APIRouter(prefix='/api')


class Send(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(min_length=1, max_length=2000)
    client_message_id: UUID

    @field_validator('content')
    @classmethod
    def trim(cls, value):
        if not value.strip():
            raise ValueError('消息不能为空')
        return value.strip()


class Read(BaseModel):
    model_config = ConfigDict(extra='forbid')
    handoff_id: UUID
    sequence: int = Field(ge=0)


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    handoff_id: UUID


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str | None = Field(default=None, max_length=2000)
    confirmed: bool = False


def conversation(db, conversation_id, lock=False):
    query = select(Conversation).where(Conversation.id == conversation_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = db.scalar(query)
    if not result:
        raise HTTPException(404, '会话不存在。')
    return result


def latest_handoff(db, c, lock=False):
    query = select(Handoff).where(Handoff.conversation_id == c.id).order_by(desc(Handoff.round)).limit(1)
    return db.scalar(query.with_for_update() if lock else query)


def add_message(db, c, role, content, client_id=None):
    c.sequence += 1
    c.revision += 1
    c.updated_at = now()
    if role == 'user':
        c.latest_user_sequence = c.sequence
    message = Message(conversation_id=c.id, sequence=c.sequence, role=role,
                      content=content, client_message_id=client_id)
    db.add(message)
    db.flush()
    return message


def open_handoff(db, c, previous, reason, event):
    db.add(Handoff(conversation_id=c.id, round=(previous.round + 1) if previous else 1,
                   reason=reason, covered_sequence=c.sequence, read_sequence=0))
    c.status = 'waiting_human'
    add_message(db, c, 'system', event)


def message_data(m):
    return {'id': m.id, 'sequence': m.sequence, 'role': m.role, 'content': m.content,
            'created_at': m.created_at.isoformat() + 'Z'}


def snapshot(db, c, merchant=False):
    result = {'id': c.id, 'status': c.status, 'revision': c.revision, 'sequence': c.sequence,
              'messages': [message_data(m) for m in db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.sequence))]}
    draft = ai_review.current_review(db, c)
    result['ai'] = {'mode': config.AI_REVIEW_MODE,
                    'simulation_date': config.AI_SIMULATION_DATE,
                    'phase': draft.status if draft and c.status == 'ai_ready' else 'idle'}
    if merchant:
        result['review'] = ai_review.review_data(draft) if draft else None
        h = latest_handoff(db, c)
        result['handoff'] = None if not h else {
            'id': h.id, 'round': h.round, 'reason': h.reason,
            'covered_sequence': h.covered_sequence,
            'created_at': h.created_at.isoformat() + 'Z',
            'ended_at': h.ended_at.isoformat() + 'Z' if h.ended_at else None,
            'summary_status': 'not_connected',
        }
    return result


def public_conversation(request, db, lock=False):
    identity = session_auth(request, db)
    return conversation(db, identity.conversation_id, lock)


@router.get('/visitor/conversation')
def visitor_get(request: Request, db: Session = Depends(get_db)):
    return snapshot(db, public_conversation(request, db))


def send_message(db, c, data, role):
    client_id = str(data.client_message_id)
    existing = db.scalar(select(Message).where(Message.conversation_id == c.id,
        Message.client_message_id == client_id).with_for_update())
    if existing:
        if existing.role != role or existing.content != data.content:
            raise HTTPException(409, '消息编号已用于其他内容，请重新发送。')
        return
    if role == 'merchant' and c.status != 'human_active':
        raise HTTPException(409, '当前会话未接手或已结束，不能回复。')
    if c.sequence >= 2000:
        raise HTTPException(409, '演示会话已达到消息上限，请联系项目负责人。')
    if role == 'user' and c.status == 'ended':
        c.status = 'ai_ready'
    return add_message(db, c, role, data.content, client_id)


@router.post('/visitor/messages')
def visitor_send(data: Send, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    c = public_conversation(request, db, lock=True)
    message = send_message(db, c, data, 'user')
    job = ai_review.enqueue(db, c) if message else None
    db.commit()
    if job:
        tasks.add_task(ai_review.run_review, job)
    return snapshot(db, c)


@router.post('/visitor/handoff')
def handoff(request: Request, db: Session = Depends(get_db)):
    c = public_conversation(request, db, lock=True)
    previous = latest_handoff(db, c, lock=True)
    if c.status not in ('waiting_human', 'human_active'):
        ai_review.invalidate_reviews(db, c)
        open_handoff(db, c, previous, '用户主动请求人工', '已申请人工接待，可以在这里继续补充文字。')
    db.commit()
    return snapshot(db, c)


@router.get('/merchant/conversations')
def merchant_list(request: Request, db: Session = Depends(get_db)):
    session_auth(request, db, merchant=True)
    pending, ended, reviews = [], [], []
    conversations = db.scalars(select(Conversation).where(
        or_(select(Handoff.id).where(Handoff.conversation_id == Conversation.id).exists(),
            select(AiReview.id).where(AiReview.conversation_id == Conversation.id).exists())
    ).order_by(desc(Conversation.updated_at)).limit(200))
    for c in conversations:
        h = latest_handoff(db, c)
        draft = ai_review.current_review(db, c)
        last = db.scalar(select(Message).where(Message.conversation_id == c.id,
            Message.role != 'system').order_by(desc(Message.sequence)).limit(1))
        row = {'id': c.id, 'status': c.status, 'preview': last.content[:60] if last else '尚未描述问题',
               'unread': bool(h and (not h.viewed or c.latest_user_sequence > h.read_sequence)),
               'updated_at': c.updated_at.isoformat() + 'Z'}
        if c.status == 'ai_ready' and draft and draft.status in ai_review.ACTIVE:
            row['review_status'] = draft.status
            reviews.append(row)
        elif c.status == 'ai_ready' and draft and draft.status == 'approved':
            row['review_status'] = 'approved'
            ended.append(row)
        elif h:
            (ended if h.ended_at else pending).append(row)
    return {'pending': pending, 'ended': ended, 'reviews': reviews}


@router.get('/merchant/conversations/{conversation_id}')
def merchant_get(conversation_id: str, request: Request, db: Session = Depends(get_db)):
    session_auth(request, db, merchant=True)
    c = conversation(db, conversation_id)
    if not latest_handoff(db, c) and not ai_review.current_review(db, c):
        raise HTTPException(404, '该会话尚未转交。')
    return snapshot(db, c, merchant=True)


@router.post('/merchant/conversations/{conversation_id}/reviews/{review_id}/{decision}')
def decide_review(conversation_id: str, review_id: UUID, decision: str, data: ReviewDecision,
                  request: Request, db: Session = Depends(get_db)):
    identity = session_auth(request, db, merchant=True)
    if decision not in ('approve', 'reject', 'handoff'):
        raise HTTPException(404, '审核动作不存在。')
    c = conversation(db, conversation_id, lock=True)
    draft = db.scalar(select(AiReview).where(AiReview.id == str(review_id),
        AiReview.conversation_id == c.id).with_for_update())
    if not draft:
        raise HTTPException(404, '审核候选不存在。')
    content = (data.content or '').strip()
    if decision == 'approve' and (not data.confirmed or not content):
        raise HTTPException(422, '请检查依据、诉求与动作，确认后发送非空回复。')
    if decision == 'approve' and draft.status == 'approved':
        if draft.final_content != content:
            raise HTTPException(409, '该候选已用其他内容发送，请刷新会话。')
        return snapshot(db, c, merchant=True)
    if (decision, draft.status) in [('reject', 'rejected'), ('handoff', 'handed_off')]:
        return snapshot(db, c, merchant=True)
    if c.status != 'ai_ready' or draft.user_sequence != c.latest_user_sequence or draft.source_revision != c.revision:
        raise HTTPException(409, '用户消息或接待状态已更新，旧候选不能处理。')
    if draft.status not in ai_review.ACTIVE:
        raise HTTPException(409, '该候选已失效，请刷新会话。')
    if decision == 'approve' and draft.status != 'ready':
        raise HTTPException(409, '候选尚未通过检查，不能发送。')
    if c.sequence >= 1999:
        raise HTTPException(409, '演示会话已达到消息上限，请联系项目负责人。')
    draft.reviewed_by, draft.reviewed_at = identity.username, now()
    if decision == 'approve':
        add_message(db, c, 'assistant', content)
        draft.status, draft.final_content = 'approved', content
        if json.loads(draft.proposal_json)['action'] == 'handoff':
            open_handoff(db, c, latest_handoff(db, c, lock=True), '商家审核后转人工', '商家已将本次问题转交人工处理。')
    elif decision == 'handoff':
        ai_review.invalidate_reviews(db, c)
        draft.status = 'handed_off'
        open_handoff(db, c, latest_handoff(db, c, lock=True), '商家审核后转人工', '商家已将本次问题转交人工处理。')
    else:
        draft.status, draft.note = 'rejected', '商家未采用该候选，可转人工继续处理。'
        ai_review.bump(c)
        draft.source_revision = c.revision
    db.commit()
    return snapshot(db, c, merchant=True)


def active_handoff(db, c, identity):
    h = latest_handoff(db, c, lock=True)
    if not h or h.id != str(identity):
        raise HTTPException(409, '交接已更新，请刷新会话。')
    return h


@router.post('/merchant/conversations/{conversation_id}/read')
def read(conversation_id: str, data: Read, request: Request, db: Session = Depends(get_db)):
    session_auth(request, db, merchant=True)
    c = conversation(db, conversation_id, lock=True)
    h = active_handoff(db, c, data.handoff_id)
    h.viewed = True
    h.read_sequence = max(h.read_sequence, min(data.sequence, c.latest_user_sequence))
    db.commit()
    return {'ok': True}


@router.post('/merchant/conversations/{conversation_id}/takeover')
def takeover(conversation_id: str, data: Action, request: Request, db: Session = Depends(get_db)):
    session_auth(request, db, merchant=True)
    c = conversation(db, conversation_id, lock=True)
    h = active_handoff(db, c, data.handoff_id)
    if c.status == 'waiting_human':
        c.status = 'human_active'
        h.taken_at = now()
        add_message(db, c, 'system', '商家已接手本次会话。')
    elif c.status != 'human_active':
        raise HTTPException(409, '当前交接不能接手。')
    db.commit()
    return snapshot(db, c, merchant=True)


@router.post('/merchant/conversations/{conversation_id}/messages')
def merchant_send(conversation_id: str, data: Send, request: Request, db: Session = Depends(get_db)):
    session_auth(request, db, merchant=True)
    c = conversation(db, conversation_id, lock=True)
    send_message(db, c, data, 'merchant')
    db.commit()
    return snapshot(db, c, merchant=True)


@router.post('/merchant/conversations/{conversation_id}/end')
def end(conversation_id: str, data: Action, request: Request, db: Session = Depends(get_db)):
    session_auth(request, db, merchant=True)
    c = conversation(db, conversation_id, lock=True)
    h = active_handoff(db, c, data.handoff_id)
    if c.status == 'human_active':
        c.status = 'ended'
        h.ended_at = now()
        add_message(db, c, 'system', '商家已结束本次处理。历史记录保留，可继续留言或再次转人工。')
        h.ended_sequence = c.sequence
    elif c.status != 'ended':
        raise HTTPException(409, '请先接手，再结束本次处理。')
    db.commit()
    return snapshot(db, c, merchant=True)
