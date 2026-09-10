import { beforeEach, describe, expect, it, vi } from 'vitest';
import { passthrough } from '@mocks/passthrough';

describe('passthrough', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubEnv('DEV', true);
  });

  it('forwards the request to the live backend and returns its response', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ from: 'live' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      );
    const res = await passthrough('kpis');
    expect(fetchSpy).toHaveBeenCalledOnce();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ from: 'live' });
  });

  it('returns 503 when the backend is unreachable (no mock fallback)', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'));
    const res = await passthrough('kpis');
    expect(res.status).toBe(503);
    const body = await res.json();
    expect(body).toEqual({ error: 'backend_unreachable', endpoint: 'kpis' });
  });

  it('returns 502 when the backend returns a non-2xx response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('down', { status: 503 }),
    );
    const res = await passthrough('kpis');
    expect(res.status).toBe(502);
  });

  it('returns 504 when the backend times out', async () => {
    vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(
        (_url: RequestInfo | URL, init?: RequestInit) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            );
          }),
      );
    const start = Date.now();
    const res = await passthrough('kpis', undefined, 50);
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(500);
    expect(res.status).toBe(504);
  });

  it('preserves path segments with slashes (e.g. model/features)', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response('[]', {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      );
    await passthrough('model/features');
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/api/model/features'),
      expect.anything(),
    );
  });

  it('returns 503 in test mode without making any fetch call', async () => {
    vi.stubEnv('DEV', false);
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const res = await passthrough('kpis');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(res.status).toBe(503);
  });
});
