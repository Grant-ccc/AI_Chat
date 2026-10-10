export type Message = { id: string; sequence: number; role: 'user' | 'merchant' | 'assistant' | 'system'; content: string; created_at: string };
export type Review = {
  id: string; status: string; mode: string; user_sequence: number; candidate: string | null; note: string | null;
  proposal: { action: string; needs: { subject: string; attribute: string; status: string; claims: { text: string; evidence: { knowledge_id: string; field: string; quote: string }[] }[] }[] } | null;
  evidence: { id: string; topic: string; facts: string; boundaries: string; sources: Record<string, { name: string; scope: string }> }[];
  final_content: string | null; reviewed_by: string | null;
};
export type Conversation = { id: string; status: 'ai_ready' | 'waiting_human' | 'human_active' | 'ended'; revision: number; sequence: number; messages: Message[]; ai: { mode: string; phase: string; simulation_date: string }; review?: Review | null; handoff?: { id: string; round: number; reason: string; covered_sequence: number; created_at: string; ended_at: string | null; summary_status: string } | null };
export type Row = { id: string; status: Conversation['status']; preview: string; unread: boolean; updated_at: string; review_status?: string };
export type Queue = { pending: Row[]; ended: Row[]; reviews: Row[] };
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export async function api<T>(path: string, body?: unknown, csrf?: string): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`/api${path}`, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', signal: controller.signal,
      headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new ApiError(response.status, typeof data.detail === 'string' ? data.detail : '提交内容不符合要求，请检查后重试。');
    return data as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(0, '连接暂时中断，已保留页面内容。请稍后重试。');
  } finally { window.clearTimeout(timeout); }
}

export const statusLabel = { ai_ready: '普通咨询', waiting_human: '等待人工', human_active: '人工接待中', ended: '本次已结束' };
export const reviewLabel: Record<string, string> = { queued: '等待生成', generating: '正在生成', ready: '待商家审核', failed: '生成失败', limited: '本地额度暂停', invalid: '检查未通过', rejected: '商家未采用', approved: '已审核发送', stale: '候选已失效', handed_off: '已转人工' };
export function aiHint(ai: Conversation['ai'] | undefined) {
  if (!ai || ai.mode === 'disabled') return 'AI 暂未启用 · 留言后可点击「转人工」获得回复。';
  const prefix = ai.mode === 'mock' ? '模拟联调 · ' : '';
  if (['queued', 'generating'].includes(ai.phase)) return prefix + '正在准备候选回复，随后由商家审核；也可直接转人工。';
  if (ai.phase === 'ready') return prefix + '候选已提交商家审核，请稍候。你仍可补充问题或转人工。';
  if (ai.phase === 'limited') return prefix + '本地测试额度已暂停，可联系项目负责人调整或转人工。';
  if (['failed', 'invalid', 'rejected', 'stale'].includes(ai.phase)) return prefix + '暂未得到可用回复，可以点击「转人工」继续处理。';
  return prefix + '回复需经商家审核后发送；需要人工时可直接转交。';
}
export const shortId = (id: string) => id.slice(0, 8).toUpperCase();
export const time = (value: string) => new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
