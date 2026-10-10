from uuid import UUID
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict, field_validator
from sqlalchemy import select, desc
from sqlalchemy.orm import Session
from .db import get_db
from .models import Conversation, Message, Handoff, now
from .security import session_auth

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
    from .simple_ai import invalidate
    invalidate(db, c)
    h = Handoff(conversation_id=c.id, round=(previous.round + 1) if previous else 1,
                reason=reason, covered_sequence=c.sequence, read_sequence=0)
    db.add(h)
    db.flush()
    from .handoff_summary import enqueue_summary
    enqueue_summary(db, c, h)
    c.status = 'waiting_human'
    add_message(db, c, 'system', event)
    return h


def message_data(m):
    return {'id': m.id, 'sequence': m.sequence, 'role': m.role, 'content': m.content,
            'created_at': m.created_at.isoformat() + 'Z'}


def snapshot(db, c, merchant=False):
    from .simple_ai import public_status
    result = {'id': c.id, 'status': c.status, 'revision': c.revision, 'sequence': c.sequence,
              'messages': [message_data(m) for m in db.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.sequence))]}
    result['ai'] = public_status(db, c)
    if merchant:
        from .handoff_summary import summary_data
        h = latest_handoff(db, c)
        result['handoff'] = None if not h else {
            'id': h.id, 'round': h.round, 'reason': h.reason,
            'covered_sequence': h.covered_sequence,
            'created_at': h.created_at.isoformat() + 'Z',
            'ended_at': h.ended_at.isoformat() + 'Z' if h.ended_at else None,
            **summary_data(db, h),
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
        return False
    if role == 'merchant' and c.status != 'human_active':
        raise HTTPException(409, '当前会话未接手或已结束，不能回复。')
    if c.sequence >= 2000:
        raise HTTPException(409, '演示会话已达到消息上限，请联系项目负责人。')
    if role == 'user' and c.status == 'ended':
        c.status = 'ai_ready'
    add_message(db, c, role, data.content, client_id)
    return True


@router.post('/visitor/messages')
def visitor_send(data: Send, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    from .simple_ai import enqueue, run
    from .handoff_summary import explicit_human_request, run_summary
    c = public_conversation(request, db, lock=True)
    task_id, summary_id = None, None
    if send_message(db, c, data, 'user'):
        if c.status == 'ai_ready' and explicit_human_request(data.content):
            h = open_handoff(db, c, latest_handoff(db, c, lock=True), '用户主动请求人工',
                             '已申请人工接待，可以在这里继续补充文字。')
            summary_id = h.id
        else:
            task_id = enqueue(db, c)
    db.commit()
    if task_id:
        background_tasks.add_task(run, task_id)
    if summary_id:
        background_tasks.add_task(run_summary, summary_id)
    return snapshot(db, c)


@router.post('/visitor/handoff')
def handoff(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    from .handoff_summary import run_summary
    c = public_conversation(request, db, lock=True)
    previous = latest_handoff(db, c, lock=True)
    summary_id = None
    if c.status not in ('waiting_human', 'human_active'):
        h = open_handoff(db, c, previous, '用户主动请求人工', '已申请人工接待，可以在这里继续补充文字。')
        summary_id = h.id
    db.commit()
    if summary_id:
        background_tasks.add_task(run_summary, summary_id)
    return snapshot(db, c)


@router.get('/merchant/conversations')
def merchant_list(request: Request, db: Session = Depends(get_db)):
    session_auth(request, db, merchant=True)
    pending, ended = [], []
    conversations = db.scalars(select(Conversation).where(
        select(Handoff.id).where(Handoff.conversation_id == Conversation.id).exists()
    ).order_by(desc(Conversation.updated_at)).limit(200))
    for c in conversations:
        h = latest_handoff(db, c)
        last = db.scalar(select(Message).where(Message.conversation_id == c.id,
            Message.role != 'system').order_by(desc(Message.sequence)).limit(1))
        row = {'id': c.id, 'status': c.status, 'preview': last.content[:60] if last else '尚未描述问题',
               'unread': not h.viewed or c.latest_user_sequence > h.read_sequence,
               'updated_at': c.updated_at.isoformat() + 'Z'}
        (ended if h.ended_at else pending).append(row)
    return {'pending': pending, 'ended': ended}


@router.get('/merchant/conversations/{conversation_id}')
def merchant_get(conversation_id: str, request: Request, db: Session = Depends(get_db)):
    session_auth(request, db, merchant=True)
    c = conversation(db, conversation_id)
    if not latest_handoff(db, c):
        raise HTTPException(404, '该会话尚未转交。')
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
    elif c.status != 'ended':
        raise HTTPException(409, '请先接手，再结束本次处理。')
    db.commit()
    return snapshot(db, c, merchant=True)
