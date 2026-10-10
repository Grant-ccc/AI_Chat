import type { Conversation, Summary } from './api';

const sections: [keyof Summary, string][] = [['request', '用户诉求'], ['known', '已知情况'], ['attempted', '已尝试操作'], ['materials', '材料情况']];
const statusText: Record<string, string> = { queued: '摘要等待生成', generating: '正在整理摘要…', failed: '摘要生成失败，请查看完整记录。', limited: '摘要暂时无法生成，请查看完整记录。', not_connected: '本次未启用摘要，请查看完整记录。', stale: '此摘要已失效，请查看最新交接。' };

export default function HandoffSummaryPanel({ conversation }: { conversation: Conversation }) {
  const h = conversation.handoff!;
  const start = h.summary_start_sequence;
  const end = h.summary_covered_sequence;
  const additional = end !== null && conversation.messages.some(m => m.role === 'user' && m.sequence > end);
  return <section className="handoff-summary" aria-label="交接摘要"><h3>交接摘要</h3>
    {h.summary_status === 'ready' && h.summary ? <>
      <p className="muted">AI 整理，接待前请核对。用户陈述尚未核实。</p>
      {sections.map(([key, title]) => <div className="summary-section" key={key}><h4>{title}</h4>
        {h.summary![key].length ? h.summary![key].map((fact, i) => <div key={i}><p>{fact.text}</p><details><summary>查看依据 · 第 {fact.sequence} 条</summary><blockquote>{fact.quote}</blockquote></details></div>) : <p className="muted">未提供</p>}
      </div>)}
    </> : <p role="status">{statusText[h.summary_status] || '请查看完整记录。'}</p>}
    {start !== null && end !== null && <small>{end >= start ? `仅整理本轮第 ${start}—${end} 条消息。` : '转交前暂无用户消息。'}摘要不影响接手。</small>}
    {additional && <p className="summary-extra">用户转交后有新补充，请查看中栏完整记录。</p>}
  </section>;
}
