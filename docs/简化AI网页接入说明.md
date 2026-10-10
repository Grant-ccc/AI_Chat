# 简化 AI 网页接入

2026-10-10。本地开发版，单进程运行。AI自然回复已接入；prompt正文与公开资料保持用户确认的修订版，不根据复测结果继续调整。

## 当前流程

用户文字落库 → 后台任务读取完整客服prompt和本轮公开对话 → 官方 `deepseek-flash` 返回自然文本 → 保存为AI助手消息 → 用户页轮询显示。非思考、非流式，temperature=0.2，输出上限1024tokens；不使用检索、分项JSON、候选审核队列或模型训练。

后端从 `docs/客服prompt模板_共创草稿.md` 的两段text块填充完整说明和公开资料。只把本轮用户、AI消息带入；商家内部知识、摘要、参考答案和测试评分不发送。开始新一轮以最近一次人工结束事件为边界；原网页聊天历史仍保留。本轮超过80条消息或输入过长时明确暂停，不静默裁掉早期条件。

回复直接发送给用户；是否准确仍取决于模型，当前已知材料提醒与多余限制说明问题保留。用户需要人工时自行点击现有“转人工”按钮，后台不会仅凭回复文案自动创建交接。等待人工和人工接待期间不调用模型；新消息及人工申请令旧请求失效，旧结果不得落入聊天。

商家继续使用现有接手、回复和结束流程，聊天中可辨认AI助手身份。真实交接摘要尚未接入。

## 本地配置

示例默认AI关闭。本机已按本次网页接入授权启用官方模式，最多累计12次、保守费用预留1元；真实网页联调已使用1次。修改下列配置需重启后端：

```dotenv
AI_WEB_MODE=deepseek
AI_SIMULATION_DATE=2026-05-04
AI_WEB_MAX_CALLS=12
AI_WEB_BUDGET_CNY=1.00
AI_PRICE_DATE=2026-10-10
```

`AI_WEB_MODE`可为disabled、mock或deepseek；mock明确显示模拟回复，不调用官方接口。Key仅从忽略的 `backend/.env` 读取，字段为 `DEEPSEEK_API_KEY`，不传到前端或提交仓库。应用只允许本版固定官方模型与官方地址；[模型及人民币价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)在接入当日核实。

网页持久账本为忽略目录 `.local/simple-web/ledger.sqlite3`，与此前离线调用分开，旧账本保留。每次发送前原子预留次数与保守费用，跨重启累计；达到次数/费用上限、历史请求失败或结果未知均暂停。没有自动重试；超时、接口异常、空输出、截断或超量不会发布，用户看到失败/暂停提示并可转人工。日期快照不是当天时也暂停，需要先重新核实价格再更新配置；不要清空账本绕过上限。

预留按UTF-8字节估算输入上限并按高峰未命中价格计算，是本地保护而非平台硬限额/实际账单。服务每次最多一个模型调用，其余任务等待时再次检查是否过期；多进程队列和公网部署不在本次范围。服务重启把中断任务标为失败，不自动补发或再次计费。

## 运行与验收

用户页 `http://localhost:5173/`，商家页 `http://localhost:5173/merchant`，沿用项目现有启停脚本。启动增量创建 `simple_ai_replies` 表，不删除已有聊天、账号或旧AI数据表。

```powershell
# 接口/预算等检查使用模拟HTTP和独立测试库，不产生真实API调用
.venv\Scripts\python.exe -X utf8 -m pytest backend/tests -q
npm --prefix frontend run build

# 原人工浏览器流程，测试服务强制disabled，不继承本机deepseek模式
.venv\Scripts\python.exe -X utf8 scripts/start_dev.py --test-db
.venv\Scripts\python.exe -X utf8 scripts/e2e_browser.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop-dev.ps1 -TestDb

# AI浏览器流程，独立测试服务强制mock，不调用官方模型
.venv\Scripts\python.exe -X utf8 scripts/start_dev.py --test-db --test-ai
.venv\Scripts\python.exe -X utf8 scripts/e2e_simple_ai.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop-dev.ps1 -TestDb
```

23项后端检查及前端生产构建通过。浏览器验收通过：模拟回复身份、刷新持久化、模拟日期、转人工后暂停、用户接口隔离、手机无横向溢出、无JavaScript页面异常；原人工闭环也通过。真实网页一次价格询问得到45/56元，刷新后保持一条AI回复，账本仅新增一次调用。这个单题联调证明接入连通，不代表整体回答质量或自动交接验收。
