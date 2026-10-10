"""模拟AI网页验收，专用测试库；拒绝真实模型模式。"""
import hashlib
import sys
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import create_engine, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.db import engine
from app.models import VisitorSession

BASE = 'http://localhost:5174'
OUT = ROOT / '.local/simple-web'
OUT.mkdir(parents=True, exist_ok=True)


with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    try:
        context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE)
        page.wait_for_load_state('networkidle')
        health = page.request.get(BASE + '/api/health').json()
        assert health['ai_mode'] == 'mock', 'Only mock mode is allowed'
        expect(page.get_by_role('button', name='转人工', exact=True)).to_be_enabled()
        cookie = next(c for c in context.cookies() if c['name'] == 'umbrella_visitor')
        target = create_engine(engine.url.set(database='umbrella_test'), hide_parameters=True)
        with target.connect() as conn:
            assert conn.scalar(select(VisitorSession.token_hash).where(
                VisitorSession.token_hash == hashlib.sha256(cookie['value'].encode()).hexdigest()))
        target.dispose()
        # Inspect the loaded controls before exercising the page.
        assert page.get_by_label('消息内容').count() == 1
        question = '模拟网页问价-' + uuid4().hex[:8]
        page.get_by_label('消息内容').fill(question)
        page.get_by_role('button', name='发送', exact=False).click()
        expect(page.locator('.message-meta').filter(has_text='AI 助手')).to_have_count(1, timeout=15000)
        expect(page.locator('.bubble').filter(has_text='【模拟 AI 回复】')).to_have_count(1)
        expect(page.locator('.history-date')).to_contain_text('2026-05-04')
        page.reload()
        page.wait_for_load_state('networkidle')
        expect(page.locator('.message-meta').filter(has_text='AI 助手')).to_have_count(1)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT / 'user-ai-mobile.png'), full_page=True)
        page.get_by_role('button', name='转人工', exact=True).click()
        expect(page.get_by_role('button', name='已申请人工')).to_be_disabled()
        page.get_by_label('消息内容').fill('等待人工时补充文字')
        page.get_by_role('button', name='发送', exact=False).click()
        expect(page.locator('.bubble').filter(has_text='等待人工时补充文字')).to_have_count(1)
        data = page.request.get(BASE + '/api/visitor/conversation').json()
        assert len([m for m in data['messages'] if m['role'] == 'assistant']) == 1
        assert data['status'] == 'waiting_human' and 'handoff' not in data
        assert not errors, errors
        print('PASS: mock AI label/reply, persistence, simulation date, human pause, public isolation, mobile layout, JS errors.')
    finally:
        browser.close()
