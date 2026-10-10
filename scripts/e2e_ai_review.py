"""在已启动的5174专用测试库服务验收模拟审核；拒绝真实模型模式。"""
import hashlib
from pathlib import Path
import sys
import uuid
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import create_engine, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.db import engine
from app.models import VisitorSession

BASE = 'http://localhost:5174'
label = 'AI审核验收-' + uuid.uuid4().hex[:8]


def send(page, content):
    page.get_by_label('消息内容').fill(content)
    page.get_by_role('button', name='发送', exact=False).click()
    expect(page.get_by_label('消息内容')).to_have_value('')


with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    user_context = browser.new_context(viewport={'width': 1280, 'height': 900})
    staff_context = browser.new_context(viewport={'width': 1440, 'height': 950})
    user, staff = user_context.new_page(), staff_context.new_page()
    errors = []
    for page in (user, staff):
        page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        assert user.request.get(BASE + '/api/health').json()['ai_mode'] == 'mock', 'Only mock testing permitted'
        user.goto(BASE)
        user.wait_for_load_state('networkidle')
        # Confirm isolated DB before writing any message.
        cookie = next(c for c in user_context.cookies() if c['name'] == 'umbrella_visitor')
        test_engine = create_engine(engine.url.set(database='umbrella_test'), hide_parameters=True)
        with test_engine.connect() as connection:
            found = connection.scalar(select(VisitorSession.token_hash).where(
                VisitorSession.token_hash == hashlib.sha256(cookie['value'].encode()).hexdigest()))
        test_engine.dispose()
        assert found, 'Server must use umbrella_test'
        print('Confirmed mock mode and isolated database.')
        staff.goto(BASE + '/merchant')
        staff.wait_for_load_state('networkidle')
        staff.get_by_label('密码', exact=True).fill('integration-password')
        staff.get_by_role('button', name='进入工作台').click()
        expect(staff.get_by_role('button', name='AI 审核')).to_be_visible()

        send(user, label + '：双面款多少钱？')
        row = staff.locator('.queue-item').filter(has_text=label)
        expect(row).to_be_visible(timeout=12000)
        row.click()
        expect(staff.get_by_label('审核后的回复')).to_have_value('这是模拟联调候选，用于验证审核流程；真实业务回答需要启用官方模型。', timeout=12000)
        expect(user.locator('.bubble')).to_have_count(1)
        expect(user.locator('.state-note')).to_contain_text('商家审核', timeout=12000)
        expect(staff.locator('.mock-note')).to_contain_text('无真实模型调用')
        expect(staff.locator('.handoff-round')).to_have_count(0)
        expect(staff.get_by_role('heading', name='本次交接', exact=True)).to_have_count(0)
        expect(staff.locator('.handoff-actions')).to_have_count(0)
        approve = staff.get_by_role('button', name='审核通过并发送', exact=True)
        expect(approve).to_be_disabled()
        staff.get_by_text('核对诉求与引用依据', exact=True).click()
        expect(staff.get_by_text('本候选没有引用事实条目。', exact=True)).to_be_visible()
        edited = '浏览器验收：这条回复已由商家编辑并确认。'
        staff.get_by_label('审核后的回复').fill(edited)
        staff.wait_for_timeout(2600)  # One polling cycle must preserve the editor.
        expect(staff.get_by_label('审核后的回复')).to_have_value(edited)
        staff.get_by_role('checkbox').check()
        expect(approve).to_be_enabled()
        (ROOT / '.local').mkdir(exist_ok=True)
        staff.screenshot(path=str(ROOT / '.local/ai-review-merchant.png'), full_page=True)
        approve.click()
        expect(user.locator('.bubble').filter(has_text=edited)).to_have_count(1, timeout=12000)
        expect(user.get_by_text('AI · 商家已审核', exact=False)).to_be_visible()

        send(user, label + '：第二个问题')
        expect(row).to_be_visible(timeout=12000)
        row.click()
        expect(staff.get_by_label('审核后的回复')).to_be_visible(timeout=12000)
        user.get_by_role('button', name='转人工', exact=True).click()
        expect(user.locator('.status')).to_have_text('等待人工', timeout=12000)
        expect(staff.locator('.review-panel')).to_have_count(0, timeout=12000)
        expect(staff.get_by_role('heading', name='本次交接', exact=True)).to_be_visible(timeout=12000)
        expect(staff.get_by_role('button', name='审核通过并发送', exact=True)).to_have_count(0)
        staff.get_by_role('button', name='待处理', exact=False).click()
        expect(row).to_be_visible(timeout=12000)
        row.click()
        staff.get_by_role('button', name='接手会话', exact=True).click()
        expect(staff.locator('.review-panel')).to_have_count(0)
        send(staff, '人工回复已收到。')
        expect(user.locator('.bubble').filter(has_text='人工回复已收到。')).to_be_visible(timeout=12000)
        staff.get_by_role('button', name='结束本次处理', exact=True).click()
        expect(user.locator('.status')).to_have_text('本次已结束', timeout=12000)

        send(user, label + '：新一轮咨询')
        expect(user.locator('.status')).to_have_text('普通咨询', timeout=12000)
        expect(user.get_by_role('button', name='转人工', exact=True)).to_be_enabled()
        staff.get_by_role('button', name='AI 审核', exact=False).click()
        expect(row).to_be_visible(timeout=12000)
        row.click()
        expect(staff.get_by_label('审核后的回复')).to_be_visible(timeout=12000)
        # Previous handoff history must not appear in the new AI consultation.
        expect(staff.locator('.handoff-round')).to_have_count(0)
        expect(staff.locator('.handoff-actions')).to_have_count(0)
        expect(staff.get_by_role('heading', name='上次交接（已结束）', exact=True)).to_have_count(0)
        staff.set_viewport_size({'width': 390, 'height': 844})
        assert staff.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        staff.screenshot(path=str(ROOT / '.local/ai-review-mobile.png'), full_page=True)
        staff.get_by_role('button', name='弃用候选', exact=True).click()
        expect(staff.locator('.review-state')).to_have_text('商家未采用', timeout=12000)
        staff.get_by_role('button', name='转人工处理', exact=True).click()
        expect(user.locator('.status')).to_have_text('等待人工', timeout=12000)
        expect(staff.locator('.handoff-round')).to_contain_text('02', timeout=12000)
        assert not errors, errors
        print('PASS: private candidate, mock label, review/editor/polling, approval, stale reply, human loop, new consultation, rejection/handoff, mobile width; no JavaScript errors.')
    finally:
        browser.close()
