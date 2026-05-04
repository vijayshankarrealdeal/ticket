const BASE_URL = import.meta.env.VITE_API_URL || '/api';

// Auth uses an httponly `wx_session` cookie set by /auth/signin and /auth/signup.
// Browsers send it automatically when we pass `credentials: 'include'`. There
// is no token in localStorage — XSS that lands cannot exfiltrate the session
// because the cookie is httponly. Keep `credentials: 'include'` on every
// request: same-origin sends the cookie either way, but cross-origin (dev
// proxy, separate domains) needs the explicit opt-in.
async function request(method, path, body, { signal } = {}) {
  const headers = { Accept: 'application/json' };
  const opts = { method, headers, signal, credentials: 'include' };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${BASE_URL}${path}`, opts);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const err = new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ''}`);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) return res.json();
  return res.text();
}

export const api = {
  get: (path, opts) => request('GET', path, undefined, opts),
  post: (path, body, opts) => request('POST', path, body ?? {}, opts),
  patch: (path, body, opts) => request('PATCH', path, body ?? {}, opts),
  put: (path, body, opts) => request('PUT', path, body ?? {}, opts),
  del: (path, opts) => request('DELETE', path, undefined, opts),
};

/**
 * Mint a short-lived bearer for places where the session cookie can't
 * follow — namely full-tab navigations to a different origin (cross-origin
 * dev: SPA on :5173 → backend on :8000) and the WS handshake. Returns null
 * on failure; callers should fall back to a same-origin redirect that
 * carries the cookie.
 */
export async function getHandoffToken() {
  try {
    const res = await api.post('/auth/handoff-token');
    return res?.token || null;
  } catch {
    return null;
  }
}

/**
 * Upload a File via the presigned MinIO/S3 URL flow (see wire.md §5).
 *   1. POST /uploads → {uploadUrl, key}
 *   2. PUT <uploadUrl> with the file bytes + matching Content-Type
 *   3. return {url, name, size, mime, key} — drop into messages.attachments
 *
 * `url` points at the backend `/uploads/sign?key=…` proxy, which 302-redirects
 * to a freshly-minted presigned GET. The bucket itself is private; never store
 * a direct MinIO URL in messages because it would either 403 or — if the
 * bucket policy ever flips public — leak globally.
 */
export async function uploadFile(file) {
  const pre = await api.post('/uploads', {
    name: file.name,
    mime: file.type || 'application/octet-stream',
    size: file.size,
  });
  const uploadUrl = pre.uploadUrl || pre.upload_url;
  const res = await fetch(uploadUrl, {
    method: 'PUT',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  });
  if (!res.ok) {
    throw new Error(`upload failed: ${res.status} ${res.statusText}`);
  }
  return {
    url: `${BASE_URL}/uploads/sign?key=${encodeURIComponent(pre.key)}`,
    name: file.name,
    size: file.size,
    mime: file.type || 'application/octet-stream',
    key: pre.key,
  };
}

export function qs(params) {
  const usp = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v === undefined || v === null || v === '') return;
    usp.set(k, String(v));
  });
  const s = usp.toString();
  return s ? `?${s}` : '';
}
