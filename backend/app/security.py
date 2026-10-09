import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from datetime import timedelta
from threading import Lock
import time
from fastapi import HTTPException, Request, Response
from sqlalchemy.orm import Session
from . import config
from .models import VisitorSession, MerchantSession, now

VISITOR_COOKIE = 'umbrella_visitor'
MERCHANT_COOKIE = 'umbrella_merchant'
attempts = defaultdict(deque)
attempt_lock = Lock()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password):
    salt = secrets.token_hex(16)
    result = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()
    return f'pbkdf2_sha256${salt}${result}'


def password_matches(password, saved):
    try:
        kind, salt, expected = saved.split('$')
        result = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000).hex()
        return kind == 'pbkdf2_sha256' and hmac.compare_digest(result, expected)
    except ValueError:
        return False


def origin_check(request: Request):
    if request.headers.get('origin') != config.APP_ORIGIN:
        raise HTTPException(403, '请求来源不允许，请从应用页面操作。')


def session_auth(request: Request, db: Session, merchant=False):
    token = request.cookies.get(MERCHANT_COOKIE if merchant else VISITOR_COOKIE, '')
    cls = MerchantSession if merchant else VisitorSession
    identity = db.get(cls, digest(token)) if token else None
    if not identity or identity.expires_at <= now():
        raise HTTPException(401, '登录或会话已失效，请重新进入。')
    if request.method not in ('GET', 'HEAD'):
        origin_check(request)
        csrf = request.headers.get('x-csrf-token', '')
        if not csrf or not hmac.compare_digest(digest(csrf), identity.csrf_hash):
            raise HTTPException(403, '操作凭据失效，请刷新页面后重试。')
    return identity


def set_cookie(response: Response, name, token, seconds):
    response.set_cookie(name, token, max_age=seconds, httponly=True,
                        secure=config.COOKIE_SECURE, samesite='strict', path='/')


def login_limit(request: Request):
    ip = request.client.host if request.client else 'unknown'
    with attempt_lock:
        if len(attempts) > 2000:
            for key in list(attempts):
                if not attempts[key] or attempts[key][-1] < time.monotonic() - 300:
                    del attempts[key]
        queue = attempts[ip]
        while queue and queue[0] < time.monotonic() - 300:
            queue.popleft()
        if len(queue) >= 5:
            raise HTTPException(429, '尝试过于频繁，请五分钟后再试。')
        queue.append(time.monotonic())
    return ip


def new_session(response, db, merchant=False, **fields):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    seconds = 8 * 3600 if merchant else 30 * 86400
    cls = MerchantSession if merchant else VisitorSession
    db.add(cls(token_hash=digest(token), csrf_hash=digest(csrf),
               expires_at=now() + timedelta(seconds=seconds), **fields))
    set_cookie(response, MERCHANT_COOKIE if merchant else VISITOR_COOKIE, token, seconds)
    return csrf
