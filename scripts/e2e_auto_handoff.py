"""自动交接网页验收，仅使用独立测试服务的模拟AI。"""
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect

BASE = 'http://localhost:5174'
OUT = Path(__file__).resolve().parents[1] / '.local/simple-web'
OUT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    try:
        user = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True)
        page = user.new_page()
        page.goto(BASE)
        assert page.request.get(BASE + '/api/health').json()['ai_mode'] == 'mock'
        expect(page.get_by_role('button', name='转人工', exact=True)).to_be_enabled()
        question = '伞坏了，我想换一把-' + uuid4().hex[:8]
        page.get_by_label('消息内容').fill(question)
        page.get_by_role('button', name='发送', exact=False).click()
        expect(page.get_by_role('button', name='已申请人工')).to_be_disabled(timeout=15000)
        public = page.request.get(BASE + '/api/visitor/conversation').json()
        assert public['status'] == 'waiting_human' and 'handoff' not in public
        admin = browser.new_context(viewport={'width': 1440, 'height': 1000})
        merchant = admin.new_page()
        merchant.goto(BASE + '/merchant')
        expect(merchant.get_by_label('密码')).to_be_enabled()
        merchant.get_by_label('密码').fill('integration-password')
        merchant.get_by_role('button', name='进入工作台').click()
        merchant.locator('.queue-item').filter(has_text=public['id'][:8].upper()).click()
        expect(merchant.get_by_label('交接摘要')).to_contain_text('【模拟摘要】', timeout=15000)
        expect(merchant.get_by_label('消息内容')).to_be_disabled()
        merchant.get_by_text('查看依据', exact=False).click()
        expect(merchant.locator('blockquote')).to_contain_text(question)
        page.get_by_label('消息内容').fill('补充：我还没有订单截图')
        page.get_by_role('button', name='发送', exact=False).click()
        expect(merchant.get_by_label('交接摘要')).to_contain_text('用户转交后有新补充', timeout=15000)
        merchant.get_by_role('button', name='接手会话', exact=True).click()
        expect(merchant.get_by_label('消息内容')).to_be_enabled()
        merchant.get_by_label('消息内容').fill('已接手，请继续补充情况。')
        merchant.get_by_role('button', name='发送', exact=False).click()
        expect(page.locator('.bubble').filter(has_text='已接手，请继续补充情况。')).to_have_count(1, timeout=15000)
        merchant.set_viewport_size({'width': 390, 'height': 844})
        assert merchant.evaluate('document.documentElement.scrollWidth <= innerWidth')
        merchant.screenshot(path=str(OUT / 'handoff-mobile.png'), full_page=True)
        merchant.get_by_role('button', name='结束本次处理').click()
        expect(merchant.get_by_role('heading', name='上次交接（已结束）')).to_be_visible()
        print('PASS: automatic transfer, private summary/evidence, supplements, takeover/reply/end, mobile layout.')
    finally:
        browser.close()
