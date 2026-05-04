import { describe, it, expect, beforeEach, vi } from 'vitest';

// `api.js` reads `import.meta.env.VITE_API_URL` at import time. We can't
// stub that after import, so just exercise the helpers that don't rely
// on it (`qs`).
import { qs } from '../api.js';

describe('qs() query-string helper', () => {
  it('returns empty string for empty params', () => {
    expect(qs({})).toBe('');
    expect(qs(undefined)).toBe('');
    expect(qs(null)).toBe('');
  });

  it('skips undefined / null / empty values', () => {
    expect(qs({ a: undefined, b: null, c: '' })).toBe('');
    expect(qs({ a: 1, b: undefined })).toBe('?a=1');
  });

  it('serializes values to strings', () => {
    expect(qs({ a: 1, b: 'two', c: true })).toBe('?a=1&b=two&c=true');
  });

  it('URL-encodes special characters', () => {
    const out = qs({ q: 'hello world & friends' });
    expect(out).toContain('hello+world');
    expect(out).toContain('%26');
  });
});

describe('uploadFile builds a stable signed-url', async () => {
  // Re-import inside the describe so we can mock fetch first.
  beforeEach(() => {
    vi.resetModules();
    globalThis.fetch = vi.fn();
  });

  it('builds an /uploads/sign URL pointing at the API base', async () => {
    globalThis.fetch
      // first call: POST /uploads
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        headers: { get: () => 'application/json' },
        json: async () => ({
          uploadUrl: 'http://minio.local/bucket/abc',
          key: 'ws-1/abc-name.png',
        }),
      })
      // second call: PUT to MinIO
      .mockResolvedValueOnce({ ok: true, status: 200, statusText: 'OK' });

    const { uploadFile } = await import('../api.js');
    const out = await uploadFile(
      new File(['hi'], 'name.png', { type: 'image/png' })
    );
    expect(out.url).toMatch(/\/uploads\/sign\?key=/);
    expect(out.url).toContain(encodeURIComponent('ws-1/abc-name.png'));
    expect(out.key).toBe('ws-1/abc-name.png');
    expect(out.mime).toBe('image/png');
  });
});
