"""真实浏览器人工闭环验收。仅连接已用--test-db启动的开发服务。"""
import hashlib
from pathlib import Path
import sys
import uuid
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import create_engine, select

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'backend'))
from app.db import engine
from app.models import VisitorSession

base = 'http://localhost:5173'
label = '浏览器验收-' + uuid.uuid4().hex[:8]
out = root / '.local'
out.mkdir(exist_ok=True)


def send(page, content):
    page.get_by_label('消息内容').fill(content)
    page.get_by_role('button', name='发送', exact=False).click()


with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    visitor = browser.new_context(viewport={'width': 1280, 'height': 900})
    merchant = browser.new_context(viewport={'width': 1440, 'height': 950})
    other = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True)
    user = visitor.new_page()
    staff = merchant.new_page()
    phone = other.new_page()
    errors = []
    session = {}
    for page in (user, staff, phone):
        page.on('pageerror', lambda error: errors.append(str(error)))
    def session_response(response):
        if response.url.endswith('/api/visitor/session') and response.status == 200:
            session.update(response.json())
    user.on('response', session_response)
    try:
        user.goto(base)
        user.wait_for_load_state('networkidle')
        expect(user.get_by_role('button', name='转人工', exact=True)).to_be_enabled()
        # Before sending anything, prove the server session lives in the dedicated test DB.
        cookie = next(cookie for cookie in visitor.cookies() if cookie['name'] == 'umbrella_visitor')
        test_engine = create_engine(engine.url.set(database='umbrella_test'), hide_parameters=True)
        with test_engine.connect() as conn:
            found = conn.scalar(select(VisitorSession.token_hash).where(VisitorSession.token_hash == hashlib.sha256(cookie['value'].encode()).hexdigest()))
        test_engine.dispose()
        if not found:
            raise RuntimeError('Server is not using umbrella_test. Stop it and restart with --test-db; no messages sent.')
        print('Confirmed isolated MySQL test database.')
        send(user, label + '：如何收伞？')
        expect(user.locator('.bubble').filter(has_text=label)).to_have_count(1)
        user.get_by_role('button', name='转人工', exact=True).click()
        expect(user.get_by_role('button', name='已申请人工')).to_be_disabled()
        staff.goto(base + '/merchant')
        staff.wait_for_load_state('networkidle')
        staff.get_by_label('密码', exact=True).fill('integration-password')
        staff.get_by_role('button', name='进入工作台').click()
        row = staff.locator('.queue-item').filter(has_text=label)
        expect(row).to_be_visible(timeout=12000)
        expect(row.locator('.unread')).to_be_visible()
        row.click()
        expect(staff.get_by_label('消息内容')).to_be_disabled()
        expect(staff.get_by_role('button', name='结束本次处理')).to_be_disabled()
        expect(row.locator('.unread')).to_have_count(0, timeout=12000)
        expect(user.get_by_role('button', name='已申请人工')).to_be_disabled()
        staff.get_by_role('button', name='接手会话').click()
        expect(user.get_by_role('button', name='人工已接手')).to_be_visible(timeout=12000)
        send(staff, '已收到你的问题，我们一起确认。')
        expect(user.locator('.bubble').filter(has_text='已收到你的问题')).to_be_visible(timeout=12000)
        # A dropped response after the database accepted the message must be retryable without duplication.
        def lose_ack(route):
            route.fetch()
            route.abort('failed')
        user.route('**/api/visitor/messages', lose_ack)
        send(user, label + '：重试同一消息')
        expect(user.get_by_label('消息内容')).to_have_value(label + '：重试同一消息')
        expect(user.locator('.composer [role=alert]')).to_contain_text('连接暂时中断')
        user.unroute('**/api/visitor/messages', lose_ack)
        user.get_by_role('button', name='发送', exact=False).click()
        expect(user.get_by_label('消息内容')).to_have_value('')
        expect(user.locator('.bubble').filter(has_text='重试同一消息')).to_have_count(1)
        visitor.set_offline(True)
        send(user, label + '：断网保留输入')
        expect(user.locator('.composer [role=alert]')).to_contain_text('连接暂时中断')
        expect(user.get_by_label('消息内容')).to_have_value(label + '：断网保留输入')
        visitor.set_offline(False)
        user.get_by_role('button', name='发送', exact=False).click()
        expect(user.get_by_label('消息内容')).to_have_value('')
        staff.get_by_role('button', name='结束本次处理').click()
        expect(user.locator('.status')).to_have_text('本次已结束', timeout=12000)
        staff.get_by_role('button', name='已结束', exact=False).click()
        expect(staff.locator('.queue-item').filter(has_text=label)).to_be_visible(timeout=12000)
        send(user, label + '：还有一个问题')
        expect(user.locator('.status')).to_have_text('等待人工')
        expect(user.get_by_role('button', name='已申请人工')).to_be_disabled()
        expect(staff.locator('.queue-item').filter(has_text='还有一个问题')).to_have_count(0, timeout=12000)
        staff.get_by_role('button', name='待处理', exact=False).click()
        expect(staff.locator('.queue-item').filter(has_text='还有一个问题')).to_be_visible(timeout=12000)
        expect(staff.locator('.handoff-round')).to_contain_text('02', timeout=12000)
        expect(staff.get_by_label('消息内容')).to_be_disabled()
        expect(staff.locator('.bubble').filter(has_text='已收到你的问题')).to_have_count(1)
        # Add enough history to scroll; a message arriving off screen must remain unread.
        user.evaluate('''async ({csrf, label}) => {
          for (let i = 0; i < 16; i++) {
            const response = await fetch('/api/visitor/messages', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({content:label+'历史 '+i+'。补充文字，检查阅读历史时的滚动和未读状态。'.repeat(8), client_message_id:crypto.randomUUID()})});
            if (!response.ok) throw new Error('History setup failed');
          }
        }''', {'csrf': session['csrf'], 'label': label})
        expect(staff.locator('.bubble').filter(has_text=label + '历史 15')).to_be_visible(timeout=12000)
        staff.locator('.history').evaluate('(el) => {el.scrollTop = 0; el.dispatchEvent(new Event("scroll"));}')
        send(user, label + '：历史阅读期间的新消息')
        expect(staff.get_by_role('button', name='有新消息', exact=False)).to_be_visible(timeout=12000)
        expect(staff.locator('.queue-item').filter(has_text='历史阅读期间的新消息').locator('.unread')).to_be_visible(timeout=12000)
        staff.get_by_role('button', name='有新消息', exact=False).click()
        expect(staff.locator('.queue-item').filter(has_text='历史阅读期间的新消息').locator('.unread')).to_have_count(0, timeout=12000)
        user_data = user.request.get(base + '/api/visitor/conversation').json()
        assert 'handoff' not in user_data
        phone.goto(base)
        phone.wait_for_load_state('networkidle')
        expect(phone.locator('.bubble')).to_have_count(0)
        assert phone.request.get(base + '/api/merchant/conversations').status == 401
        assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
        phone.screenshot(path=str(out / 'user-mobile.png'), full_page=True)
        staff.screenshot(path=str(out / 'merchant-desktop.png'), full_page=True)
        staff.set_viewport_size({'width': 390, 'height': 844})
        assert staff.evaluate('document.documentElement.scrollWidth <= innerWidth')
        staff.screenshot(path=str(out / 'merchant-mobile.png'), full_page=True)
        staff.get_by_role('button', name='退出登录').click()
        expect(staff.get_by_label('密码', exact=True)).to_be_visible(timeout=12000)
        assert staff.request.get(base + '/api/merchant/conversations').status == 401
        assert not errors, errors
        print('PASS: handoff, read, takeover, reply, end, new round, lost acknowledgment retry, offline draft, unread while reading, visitor isolation, logout, mobile widths, JavaScript errors.')
    finally:
        browser.close()
