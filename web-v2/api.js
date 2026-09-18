// 与旧版分别保存会话；使用同源 API，支持后台部署地址变化。
const TOKEN_KEY = 'zhicang-v2-token';
export function hasSession() { return !!sessionStorage.getItem(TOKEN_KEY); }
export function getToken() { return sessionStorage.getItem(TOKEN_KEY) || ''; }
export function setToken(token) { sessionStorage.setItem(TOKEN_KEY, token); }
export function clearToken() { sessionStorage.removeItem(TOKEN_KEY); }
export function unwrap(value) {
  return value && typeof value === 'object' && 'code' in value && 'data' in value ? value.data : value;
}
export async function api(path, { method = 'GET', body, timeout = 20000, signal } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  const headers = { Accept: 'application/json' };
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  try {
    const response = await fetch(path, { method, body, headers, signal: controller.signal });
    const raw = await response.text();
    let data;
    try { data = raw ? JSON.parse(raw) : {}; }
    catch { throw new Error(response.status === 504 ? '识别服务响应超时，图片可能已保存。请稍后重试。' : '服务返回异常，请稍后重试。'); }
    const result = unwrap(data);
    if (response.status === 401 && path !== '/api/auth/login') {
      clearToken();
      window.dispatchEvent(new Event('session-expired'));
    }
    if (!response.ok || (data.code && data.code !== 0)) {
      const detail = result?.detail ?? data.detail;
      throw new Error(typeof detail === 'string' ? detail : (data.message && data.message !== 'ok' ? data.message : `请求失败（${response.status}）`));
    }
    return result;
  } catch (error) {
    if (error.name === 'AbortError') {
      const cancelled = !!signal?.aborted;
      const aborted = new Error(cancelled ? '已停止等待识别结果。' : '等待响应超时；提交操作可能已完成，请先刷新单据列表核对，避免重复提交。');
      aborted.code = cancelled ? 'CANCELLED' : 'TIMEOUT';
      throw aborted;
    }
    if (error instanceof TypeError) throw new Error('无法连接服务，请检查后端是否已启动。');
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}
