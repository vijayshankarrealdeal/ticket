const BASE_URL = import.meta.env.VITE_API_URL || '/api';

function authHeader() {
  try {
    const raw = localStorage.getItem('workspacex.auth');
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    const token = parsed?.token || parsed?.accessToken;
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

async function request(method, path, body, { signal } = {}) {
  const headers = { Accept: 'application/json', ...authHeader() };
  const opts = { method, headers, signal };
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
 * Upload a File via the presigned MinIO/S3 URL flow (see wire.md §5).
 *   1. POST /uploads → {uploadUrl, publicUrl, key}
 *   2. PUT <uploadUrl> with the file bytes + matching Content-Type
 *   3. return {publicUrl, name, size, mime} — drop into messages.attachments
 */
export async function uploadFile(file) {
  const pre = await api.post('/uploads', {
    name: file.name,
    mime: file.type || 'application/octet-stream',
    size: file.size,
  });
  const uploadUrl = pre.uploadUrl || pre.upload_url;
  const publicUrl = pre.publicUrl || pre.public_url;
  const res = await fetch(uploadUrl, {
    method: 'PUT',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  });
  if (!res.ok) {
    throw new Error(`upload failed: ${res.status} ${res.statusText}`);
  }
  return {
    url: publicUrl,
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
