import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, ApiError, statusLabel, reviewLabel, aiHint, shortId, time } from './api';
import ReviewPanel from './ReviewPanel';
import type { Conversation, Queue } from './api';

function WindowMark({ large = false }: { large?: boolean }) {
  return <svg className={large ? 'window-mark large' : 'window-mark'} viewBox="0 0 96 112" fill="none" aria-hidden="true">
    <path d="M5 107V48a43 43 0 0 1 86 0v59Z" fill="#eee4ca" stroke="currentColor" strokeWidth="3" />
    <path d="M48 6v101M5 65h86M18 18l60 89M78 18l-60 89M5 48h86" stroke="currentColor" strokeWidth="2" />
    <path d="M48 7v41H7A41 41 0 0 1 48 7Z" fill="#c28762" fillOpacity=".55" />
    <path d="m48 65 28 42H48Z" fill="#386359" fillOpacity=".75" />
    <path d="m48 65-28 42H6V65Z" fill="#a34839" fillOpacity=".65" />
    <circle cx="48" cy="48" r="14" fill="#e4c978" stroke="currentColor" strokeWidth="2" />
  </svg>;
}
function Header({ merchant = false, logout }: { merchant?: boolean; logout?: () => void }) {
  return <header className="topbar"><a className="brand" href="/"><WindowMark /><span>花窗伞<span className="brand-sub">客服 · 二团历史模拟</span></span></a><div className="header-right"><span className="edition">一期试用 / 文字接待</span>{logout ? <button className="text-button" onClick={logout}>退出登录</button> : <a className="text-button" href={merchant ? '/' : '/merchant'}>{merchant ? '返回用户页' : '商家入口'} <span aria-hidden="true">↗</span></a>}</div></header>;
}
function Notice({ children }: { children: React.ReactNode }) { return <div className="notice" role="alert">{children}</div>; }
function Status({ state }: { state: Conversation['status'] }) { return <span className={`status ${state}`}><i />{statusLabel[state]}</span>; }

// A single polling loop per view; pause in background tabs and refresh on return.
function usePoll(task: () => Promise<void>, enabled: boolean) {
  const current = useRef(task); current.current = task;
  useEffect(() => {
    if (!enabled) return;
    let disposed = false, running = false;
    let timer: number;
    async function run() {
      if (disposed || running || document.hidden) return;
      running = true;
      try { await current.current(); } finally {
        running = false;
        if (!disposed) timer = window.setTimeout(run, 2000);
      }
    }
    function visible() { window.clearTimeout(timer); if (!document.hidden) void run(); }
    document.addEventListener('visibilitychange', visible);
    void run();
    return () => { disposed = true; window.clearTimeout(timer); document.removeEventListener('visibilitychange', visible); };
  }, [enabled]);
}

function Messages({ conversation, merchant = false, onSeen }: { conversation: Conversation; merchant?: boolean; onSeen?: (sequence: number) => void }) {
  const scroll = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const [newMessages, setNewMessages] = useState(false);
  const seenCallback = useRef(onSeen); seenCallback.current = onSeen;
  const previousSequence = useRef(conversation.sequence);
  useEffect(() => {
    const root = scroll.current;
    if (!root) return;
    if (atBottom.current) { root.scrollTop = root.scrollHeight; setNewMessages(false); }
    else if (conversation.sequence > previousSequence.current) setNewMessages(true);
    previousSequence.current = conversation.sequence;
    const observer = new IntersectionObserver(entries => {
      if (document.hidden) return;
      const visible = entries.filter(entry => entry.isIntersecting).map(entry => Number((entry.target as HTMLElement).dataset.sequence));
      if (visible.length) seenCallback.current?.(Math.max(...visible));
    }, { root, threshold: 0.1 });
    root.querySelectorAll('[data-sequence]').forEach(element => observer.observe(element));
    return () => observer.disconnect();
  }, [conversation.sequence]);
  return <div className="history-wrap"><div ref={scroll} className="history" aria-label="聊天记录" onScroll={() => {
    const el = scroll.current!;
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    if (atBottom.current) setNewMessages(false);
  }}>
    <div className="history-date">二团历史模拟 · 业务日期 {conversation.ai.simulation_date} · 消息时间为实际发送时间</div>
    {conversation.messages.length === 0 && <div className="welcome"><WindowMark large /><p className="eyebrow">在这里，慢慢说</p><h2>关于你的花窗伞</h2><p>回复由商家审核后发送。<br />也可以直接申请人工接待。</p></div>}
    {conversation.messages.map(message => message.role === 'system' ? <div className="system-event" key={message.id} data-sequence={message.sequence}>{message.content}</div> : <article key={message.id} data-sequence={message.sequence} className={`message ${message.role === (merchant ? 'merchant' : 'user') ? 'own' : 'other'}`}>
      <div className="message-meta">{message.role === 'user' ? (merchant ? '访客' : '你') : message.role === 'assistant' ? 'AI · 商家已审核' : '商家'}<time dateTime={message.created_at}>{time(message.created_at)}</time></div>
      <div className="bubble">{message.content}</div>
    </article>)}
  </div>{newMessages && <button className="new-messages" onClick={() => { atBottom.current = true; scroll.current!.scrollTop = scroll.current!.scrollHeight; setNewMessages(false); }}>有新消息 · 查看 ↓</button>}</div>;
}
function Composer({ send, disabled = false, hint }: { send: (content: string, id: string) => Promise<void>; disabled?: boolean; hint: string }) {
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const attempt = useRef<{ content: string; id: string } | null>(null);
  const sending = useRef(false);
  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const content = draft.trim();
    if (disabled || sending.current || !content) return;
    if (!attempt.current || attempt.current.content !== content) attempt.current = { content, id: crypto.randomUUID() };
    sending.current = true; setBusy(true); setError('');
    try { await send(content, attempt.current.id); setDraft(''); attempt.current = null; }
    catch (e) { setError((e as Error).message); }
    finally { sending.current = false; setBusy(false); }
  }
  return <form className="composer" onSubmit={submit}>{error && <Notice>{error}</Notice>}<label className="sr-only" htmlFor="message-draft">消息内容</label><textarea id="message-draft" placeholder={disabled ? hint : '写下你的问题…'} value={draft} maxLength={2000} disabled={disabled || busy} onChange={e => setDraft(e.target.value)} onKeyDown={e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit(); }
  }} /><div className="composer-footer"><span>{hint}<span className="keyboard-hint"> · Enter 发送，Shift + Enter 换行</span></span><div><span className="counter">{draft.length}/2000</span><button className="primary" disabled={disabled || busy || !draft.trim()}>{busy ? '发送中…' : '发送'} <span aria-hidden="true">↑</span></button></div></div></form>;
}
function Visitor() {
  const [csrf, setCsrf] = useState('');
  const [data, setData] = useState<Conversation | null>(null);
  const [error, setError] = useState('');
  const [expired, setExpired] = useState(false);
  const [busy, setBusy] = useState(false);
  const live = useRef(true);
  useEffect(() => { live.current = true; void bootstrap(); return () => { live.current = false; }; }, []);
  const accept = (value: Conversation) => setData(old => old && old.id === value.id && old.revision > value.revision ? old : value);
  function failed(e: unknown) { if (!live.current) return; setError((e as Error).message); if (e instanceof ApiError && e.status === 401) { setExpired(true); setCsrf(''); } }
  async function bootstrap() {
    setBusy(true); setError('');
    try { const session = await api<{ csrf: string }>('/visitor/session', {}); if (live.current) { setCsrf(session.csrf); setExpired(false); } }
    catch (e) { failed(e); } finally { if (live.current) setBusy(false); }
  }
  usePoll(async () => { try { const value = await api<Conversation>('/visitor/conversation'); if (live.current) { accept(value); setError(''); } } catch (e) { failed(e); } }, !!csrf);
  async function handoff() {
    setBusy(true); setError('');
    try { accept(await api<Conversation>('/visitor/handoff', {}, csrf)); } catch (e) { failed(e); } finally { setBusy(false); }
  }
  const state = data?.status;
  return <><Header /><main className="visitor-layout"><aside className="visitor-intro"><p className="eyebrow">花窗伞 · 客服试用</p><h1>一把伞的事，<br />在这里聊。</h1><p className="intro-copy">商品疑问、使用困惑、售后沟通。<br />留下一段文字，我们一起处理。</p><div className="intro-rule" /><div className="small-note"><span className="number">01</span><p>支持文字咨询与人工接待。<br />AI 候选需经商家审核。</p></div><div className="small-note"><span className="number">02</span><p>同一浏览器保留历史记录。<br />请勿在此发送密码等敏感信息。</p></div><div className="intro-bottom">FLOWER WINDOW UMBRELLA<span>每一次咨询，认真接住。</span></div></aside><section className="chat-panel" aria-label="用户会话"><div className="chat-heading"><div><p className="eyebrow">你的专属会话</p><h2>花窗伞客服</h2></div><div className="chat-actions">{state && <Status state={state} />}<button className="secondary" onClick={handoff} disabled={!csrf || !data || busy || state === 'waiting_human' || state === 'human_active'}>{busy ? '处理中…' : state === 'waiting_human' ? '已申请人工' : state === 'human_active' ? '人工已接手' : '转人工'}</button></div></div>
      <div className="state-note">{state === 'waiting_human' ? '已申请人工，可以继续补充问题。' : state === 'human_active' ? '商家正在接待，请在下方继续沟通。' : state === 'ended' ? '本次处理已结束，历史保留。可以继续留言，需要人工时请再次点击“转人工”。' : aiHint(data?.ai)}</div>
      {error && <Notice>{error} {expired ? <button className="text-button" disabled={busy} onClick={async () => { setBusy(true); try { await api('/visitor/reset', {}); setData(null); await bootstrap(); } catch (e) { failed(e); setBusy(false); } }}>建立新会话（原记录无法恢复）</button> : !csrf && <button className="text-button" disabled={busy} onClick={bootstrap}>重试连接</button>}</Notice>}
      {data ? <Messages key={data.id} conversation={data} /> : <div className="empty-state">{expired ? '会话凭据已失效' : '正在连接你的会话…'}</div>}
      <Composer disabled={!csrf || !data || expired} hint="仅支持文字" send={async (content, id) => { try { accept(await api<Conversation>('/visitor/messages', { content, client_message_id: id }, csrf)); setError(''); } catch (e) { failed(e); throw e; } }} />
    </section></main><footer className="page-footer">花窗伞客服 · 二团历史模拟 <span>一期试用</span></footer></>;
}
function Merchant() {
  const [csrf, setCsrf] = useState('');
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [queue, setQueue] = useState<Queue>({ pending: [], ended: [], reviews: [] });
  const [tab, setTab] = useState<'pending' | 'ended' | 'reviews'>('reviews');
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<Conversation | null>(null);
  const selectedRef = useRef('');
  const active = useRef(false);
  const readAck = useRef('');
  const readBusy = useRef(false);
  const seen = useRef(0);
  const detailRef = useRef(detail); detailRef.current = detail;
  function failed(e: unknown) {
    if (!active.current) return;
    setError((e as Error).message);
    if (e instanceof ApiError && e.status === 401) { active.current = false; setCsrf(''); setDetail(null); selectedRef.current = ''; setSelected(''); setQueue({ pending: [], ended: [], reviews: [] }); }
  }
  useEffect(() => {
    let alive = true;
    api<{ csrf: string }>('/merchant/session').then(session => { if (alive) { active.current = true; setCsrf(session.csrf); } }).catch(e => { if (alive && (!(e instanceof ApiError) || e.status !== 401)) setError(e.message); }).finally(() => { if (alive) setChecking(false); });
    return () => { alive = false; active.current = false; };
  }, []);
  function accept(value: Conversation) {
    if (!active.current || selectedRef.current !== value.id) return;
    setDetail(old => old?.id === value.id && old.revision > value.revision ? old : value);
  }
  async function refresh() {
    try {
      const rows = await api<Queue>('/merchant/conversations');
      if (!active.current) return;
      setQueue(rows);
      const id = selectedRef.current;
      if (id) accept(await api<Conversation>(`/merchant/conversations/${id}`));
      if (active.current) setError('');
    } catch (e) { failed(e); }
  }
  usePoll(refresh, !!csrf);
  async function choose(id: string) {
    selectedRef.current = id; setSelected(id); setDetail(null); seen.current = 0; readAck.current = ''; setError('');
    try { accept(await api<Conversation>(`/merchant/conversations/${id}`)); } catch (e) { failed(e); }
  }
  const markRead = useCallback(async (sequence: number) => {
    const value = detailRef.current;
    if (!value?.handoff || !active.current || document.hidden || selectedRef.current !== value.id) return;
    seen.current = Math.max(seen.current, sequence);
    const key = `${value.id}:${value.handoff.id}:${seen.current}`;
    if (readAck.current === key || readBusy.current) return;
    readBusy.current = true;
    try {
      await api(`/merchant/conversations/${value.id}/read`, { handoff_id: value.handoff.id, sequence: seen.current }, csrf);
      readAck.current = key;
    } catch (e) { failed(e); } finally { readBusy.current = false; }
  }, [csrf]);
  useEffect(() => { if (detail) void markRead(seen.current); }, [detail, markRead]);
  async function action(name: 'takeover' | 'end') {
    if (!detail?.handoff || busy) return;
    setBusy(true); setError('');
    try { accept(await api<Conversation>(`/merchant/conversations/${detail.id}/${name}`, { handoff_id: detail.handoff.id }, csrf)); await refresh(); } catch (e) { failed(e); } finally { setBusy(false); }
  }
  async function reviewAction(name: 'approve' | 'reject' | 'handoff', content?: string) {
    if (!detail?.review || busy) return;
    setBusy(true); setError('');
    try { accept(await api<Conversation>(`/merchant/conversations/${detail.id}/reviews/${detail.review.id}/${name}`, { content: content || null, confirmed: name === 'approve' }, csrf)); await refresh(); } catch (e) { failed(e); } finally { setBusy(false); }
  }
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    const form = new FormData(event.currentTarget);
    try { const value = await api<{ csrf: string }>('/merchant/login', { username: form.get('username'), password: form.get('password') }); active.current = true; setCsrf(value.csrf); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function logout() {
    if (busy) return; setBusy(true);
    try { await api('/merchant/logout', {}, csrf); active.current = false; setCsrf(''); setSelected(''); selectedRef.current = ''; setDetail(null); setQueue({ pending: [], ended: [], reviews: [] }); setError(''); }
    catch (e) { failed(e); } finally { setBusy(false); }
  }
  if (!csrf) return <><Header merchant /><main className="login-layout"><section className="login-intro"><WindowMark large /><p className="eyebrow">商家工作台</p><h1>把每一次咨询，<br />接着聊完。</h1><p>查看历史，接手会话，回复与结束处理。<br />一处完成文字接待。</p></section><form className="login-card" onSubmit={login}><p className="eyebrow">MERCHANT ACCESS</p><h2>登录工作台</h2><p className="muted">使用项目负责人提供的商家账号。</p>{error && <Notice>{error}</Notice>}<label htmlFor="username">账号</label><input id="username" name="username" autoComplete="username" defaultValue="merchant" required maxLength={80} disabled={busy || checking} /><label htmlFor="password">密码</label><input id="password" name="password" type="password" autoComplete="current-password" required maxLength={128} disabled={busy || checking} /><button className="primary" disabled={busy || checking}>{checking ? '检查登录状态…' : busy ? '登录中…' : '进入工作台 →'}</button><span className="login-footnote">仅供商家接待使用</span></form></main></>;
  const state = detail?.status;
  const h = detail?.handoff;
  return <><Header merchant logout={logout} /><main className="workspace"><aside className="queue-panel"><div className="queue-heading"><p className="eyebrow">INBOX</p><h1>接待台<span>{queue.pending.length} 待处理</span></h1></div><div className="queue-tabs"><button className={tab === 'reviews' ? 'active' : ''} onClick={() => setTab('reviews')}>AI 审核 <span>{queue.reviews.length}</span></button><button className={tab === 'pending' ? 'active' : ''} onClick={() => setTab('pending')}>待处理 <span>{queue.pending.length}</span></button><button className={tab === 'ended' ? 'active' : ''} onClick={() => setTab('ended')}>已结束 <span>{queue.ended.length}</span></button></div><div className="queue-list">{queue[tab].length === 0 && <div className="queue-empty">{tab === 'reviews' ? '暂无待审核候选' : tab === 'pending' ? '暂时没有待处理会话' : '暂无已结束会话'}<span>新的咨询会在这里出现。</span></div>}{queue[tab].map(row => <button key={row.id} className={`queue-item ${selected === row.id ? 'selected' : ''}`} onClick={() => void choose(row.id)}><div className="queue-item-head"><strong>访客 {shortId(row.id)}</strong><time>{time(row.updated_at)}</time></div><p>{row.preview}</p><div className="queue-item-bottom"><span>{row.review_status ? reviewLabel[row.review_status] : statusLabel[row.status]}</span>{row.unread && <span className="unread">未读 <i /></span>}</div></button>)}</div><div className="queue-footer"><i />约 2 秒同步一次</div></aside><section className="workspace-chat">{error && <Notice>{error}</Notice>}{detail ? <><div className="chat-heading"><div><p className="eyebrow">会话记录</p><h2>访客 {shortId(detail.id)}</h2></div><Status state={detail.status} /></div><Messages key={detail.id} conversation={detail} merchant onSeen={sequence => void markRead(sequence)} /><Composer key={detail.id} disabled={state !== 'human_active' || busy} hint={state === 'human_active' ? '商家回复 · 仅支持文字' : state === 'waiting_human' ? '接手后可回复' : state === 'ai_ready' ? '请在审核区处理候选或转人工' : '本次处理已结束，记录只读'} send={async (content, id) => { try { accept(await api<Conversation>(`/merchant/conversations/${detail.id}/messages`, { content, client_message_id: id }, csrf)); await refresh(); } catch (e) { failed(e); throw e; } }} /></> : <div className="empty-state"><WindowMark large /><h2>{selected ? '正在读取会话…' : '选择一段会话'}</h2><p>完整记录在这里展开。<br />查看不会自动接手会话。</p></div>}</section><aside className="handoff-panel">{detail?.review && <ReviewPanel key={`${detail.review.id}:${detail.review.status}`} draft={detail.review} busy={busy} decide={reviewAction} />}<p className="eyebrow">HANDOFF</p><h2>{h?.ended_at ? '上次交接（已结束）' : '本次交接'}</h2>{h && detail ? <><div className="handoff-round">第 <strong>{String(h.round).padStart(2, '0')}</strong> 次交接</div><dl><dt>转交原因</dt><dd>{h.reason}</dd><dt>转交时间</dt><dd>{new Date(h.created_at).toLocaleString('zh-CN', { hour12: false })}</dd><dt>转交时记录范围</dt><dd>{h.covered_sequence ? `第 1—${h.covered_sequence} 条消息` : '转交前暂无消息'}</dd></dl><div className="summary-placeholder"><span>AI 摘要</span><p>尚未接入</p><small>请查看中栏完整会话记录。</small></div><div className="handoff-actions"><button className="primary" onClick={() => void action('takeover')} disabled={busy || state !== 'waiting_human'}>{state === 'human_active' ? '已接手' : '接手会话'}</button><button className="secondary" onClick={() => void action('end')} disabled={busy || state !== 'human_active'}>结束本次处理</button><p>结束后保留全部历史。<br />用户主动转人工后回到待处理。</p></div></> : <p className="muted">选择会话后查看交接信息。</p>}</aside></main></>;
}
export default function App() { return window.location.pathname.startsWith('/merchant') ? <Merchant /> : <Visitor />; }
