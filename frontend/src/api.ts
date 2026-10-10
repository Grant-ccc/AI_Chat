export type Message = { id: string; sequence: number; role: 'user' | 'merchant' | 'system'; content: string; created_at: string };
export type Conversation = { id: string; status: 'ai_ready' | 'waiting_human' | 'human_active' | 'ended'; revision: number; sequence: number; messages: Message[]; handoff?: { id: string; round: number; reason: string; covered_sequence: number; created_at: string; ended_at: string | null; summary_status: string } };
export type Row = { id: string; status: Conversation['status']; preview: string; unread: boolean; updated_at: string };
export type Queue = { pending: Row[]; ended: Row[] };
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

export const statusLabel = { ai_ready: '可留言', waiting_human: '等待人工', human_active: '人工接待中', ended: '本次已结束' };
export const shortId = (id: string) => id.slice(0, 8).toUpperCase();
export const time = (value: string) => new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
