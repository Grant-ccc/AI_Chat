import { useState } from 'react';
import type { Review } from './api';
import { reviewLabel } from './api';

const actionLabel: Record<string, string> = { answer: '回答', clarify: '追问一个条件', handoff: '回复后转人工', insufficient: '说明资料不足', out_of_scope: '说明服务范围' };
const needLabel: Record<string, string> = { supported: '候选声称有依据', missing_user: '需补充用户条件', missing_knowledge: '缺少资料', requires_realtime: '需实时核实', human_decision: '需人工决定', out_of_scope: '超出服务范围' };

export default function ReviewPanel({ draft, busy, decide }: {
  draft: Review; busy: boolean;
  decide: (action: 'approve' | 'reject' | 'handoff', content?: string) => Promise<void>;
}) {
  const [content, setContent] = useState(draft.candidate || '');
  const [confirmed, setConfirmed] = useState(false);
  const actionable = ['queued', 'generating', 'ready', 'failed', 'invalid', 'rejected', 'limited'].includes(draft.status);
  return <section className="review-panel" aria-label="AI候选审核">
    <p className="eyebrow">REVIEW BEFORE SEND</p><h2>AI 候选审核</h2>
    <div className="review-state">{reviewLabel[draft.status] || draft.status}</div>
    {draft.mode === 'mock' && <p className="mock-note">模拟联调 · 无真实模型调用<br />此候选只用于测试审核流程。</p>}
    <p className="review-note">{draft.note || '候选尚未发给用户。可以直接转人工，无需等待生成。'}</p>
    {draft.status === 'ready' && draft.proposal && <>
      <div className="review-decision"><span>建议动作</span><strong>{actionLabel[draft.proposal.action]}</strong></div>
      <details className="review-evidence"><summary>核对诉求与引用依据</summary>
        {draft.proposal.needs.map((need, index) => <div className="review-need" key={index}>
          <strong>{need.subject} · {need.attribute}</strong><small>{needLabel[need.status]}</small>
          {need.claims.map((claim, i) => <div key={i}><p>{claim.text}</p>{claim.evidence.map((evidence, j) =>
            <blockquote key={j}><span>{evidence.knowledge_id} · {evidence.field === 'facts' ? '事实' : '回答边界'}</span>{evidence.quote}</blockquote>)}</div>)}
        </div>)}
        <h3>本次检索资料</h3><p>检查是否遗漏诉求，原文是否真正支持结论。编号匹配不能代替事实判断。</p>
        {draft.evidence.length === 0 && <p>本候选没有引用事实条目。</p>}
        {draft.evidence.map(doc => <details key={doc.id}><summary>{doc.id} · {doc.topic}</summary><p>{doc.facts}</p><p className="muted">{doc.boundaries}</p><small>{Object.entries(doc.sources).map(([id, source]) => `${id}：${source.name}（${source.scope}）`).join('；')}</small></details>)}
      </details>
      <label className="review-editor-label" htmlFor="review-content">审核后的回复</label>
      <textarea id="review-content" value={content} maxLength={2000} disabled={busy} onChange={e => { setContent(e.target.value); setConfirmed(false); }} />
      <label className="review-confirm"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} /><span>我已核对事实、全部诉求和处理动作，确认可发送。</span></label>
      <button className="primary" disabled={busy || !confirmed || !content.trim()} onClick={() => void decide('approve', content)}>{busy ? '处理中…' : draft.proposal.action === 'handoff' ? '审核发送并转人工' : '审核通过并发送'}</button>
    </>}
    {actionable && <div className="review-actions">{draft.status !== 'rejected' && <button className="text-button" disabled={busy} onClick={() => void decide('reject')}>弃用候选</button>}<button className="secondary" disabled={busy} onClick={() => void decide('handoff')}>转人工处理</button></div>}
    {draft.status === 'approved' && <p className="muted">已由商家审核发送。内容保存在会话记录中。</p>}
    {draft.status === 'stale' && <p className="muted">用户消息或接待状态已变化，此候选不能发送。</p>}
  </section>;
}
