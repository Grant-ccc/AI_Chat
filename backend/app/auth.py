import secrets
from fastapi import APIRouter, Depends, Request, Response, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.orm import Session
from . import config
from .db import get_db
from .models import Conversation, VisitorSession, Merchant, MerchantSession, now
from .security import (origin_check, digest, new_session, session_auth, password_matches,
                       login_limit, attempts, attempt_lock, VISITOR_COOKIE, MERCHANT_COOKIE)

router = APIRouter(prefix='/api')


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


@router.post('/visitor/session')
def visitor_session(request: Request, response: Response, db: Session = Depends(get_db)):
    origin_check(request)
    token = request.cookies.get(VISITOR_COOKIE)
    existing = db.get(VisitorSession, digest(token)) if token else None
    if token and (not existing or existing.expires_at <= now()):
        raise HTTPException(401, '原会话凭据失效；请确认后建立新会话。')
    if existing:
        csrf = secrets.token_urlsafe(32)
        existing.csrf_hash = digest(csrf)
        conversation_id = existing.conversation_id
    else:
        c = Conversation()
        db.add(c)
        db.flush()
        conversation_id = c.id
        csrf = new_session(response, db, conversation_id=c.id)
    db.commit()
    return {'csrf': csrf, 'conversation_id': conversation_id}


@router.post('/visitor/reset')
def reset_visitor(request: Request, response: Response):
    # 仅清除本浏览器凭据，旧会话与消息仍保存在数据库；不可恢复给其他访客。
    origin_check(request)
    response.delete_cookie(VISITOR_COOKIE, path='/')
    return {'ok': True}


@router.post('/merchant/login')
def login(data: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    origin_check(request)
    ip = login_limit(request)
    account = db.get(Merchant, data.username)
    if not account or not password_matches(data.password, account.password_hash):
        raise HTTPException(401, '账号或密码不正确。')
    old_token = request.cookies.get(MERCHANT_COOKIE)
    old = db.get(MerchantSession, digest(old_token)) if old_token else None
    if old:
        db.delete(old)
    csrf = new_session(response, db, merchant=True, username=account.username)
    db.commit()
    with attempt_lock:
        attempts.pop(ip, None)
    return {'csrf': csrf, 'username': account.username}


@router.get('/merchant/session')
def merchant_session(request: Request, db: Session = Depends(get_db)):
    identity = session_auth(request, db, merchant=True)
    # 同源页面读取当前登录态并重新取操作令牌；令牌轮换不会改变接待状态。
    origin = request.headers.get('origin')
    if origin and origin != config.APP_ORIGIN:
        raise HTTPException(403, '请求来源不允许。')
    csrf = secrets.token_urlsafe(32)
    identity.csrf_hash = digest(csrf)
    db.commit()
    return {'csrf': csrf, 'username': identity.username}


@router.post('/merchant/logout')
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    identity = session_auth(request, db, merchant=True)
    db.delete(identity)
    db.commit()
    response.delete_cookie(MERCHANT_COOKIE, path='/')
    return {'ok': True}
