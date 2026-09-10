import { beforeEach, describe, expect, it, vi } from 'vitest';
import { passthrough } from '@mocks/passthrough';

describe('passthrough', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // Make the passthrough branch run (it bails out when DEV is false).
    vi.stubEnv('DEV', true);
  });

  it('returns the live backend response when /health-style fetch succeeds', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ from: 'live' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      );
    const mock = {
      status: 200,
      headers: { get: () => 'application/json' },
      text: () => Promise.resolve(JSON.stringify({ from: 'mock' })),
    };
    const res = await passthrough('kpis', mock as unknown as Response);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ from: 'live' });
    expect(fetchSpy).toHaveBeenCalledOnce();
  });

  it('falls back to the mock when the backend is unreachable', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'));
    const mock = {
      status: 200,
      headers: { get: () => 'application/json' },
      text: () =>
        Promise.resolve(JSON.stringify({ from: 'mock', count: 7 })),
    };
    const res = await passthrough('kpis', mock as unknown as Response);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ from: 'mock', count: 7 });
  });

  it('falls back to the mock on non-2xx', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('down', { status: 503 }),
    );
    const mock = {
      status: 200,
      headers: { get: () => 'application/json' },
      text: () => Promise.resolve(JSON.stringify({ from: 'mock' })),
    };
    const res = await passthrough('kpis', mock as unknown as Response);
    const body = await res.json();
    expect(body).toEqual({ from: 'mock' });
  });

  it('times out fast on slow backends', async () => {
    // Never resolves — abort should fire after 1500 ms by default.
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
    const mock = {
      status: 200,
      headers: { get: () => 'application/json' },
      text: () => Promise.resolve(JSON.stringify({ from: 'mock' })),
    };
    const start = Date.now();
    const res = await passthrough('kpis', mock as unknown as Response, 50);
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(500);
    const body = await res.json();
    expect(body).toEqual({ from: 'mock' });
  });

  it('returns the mock directly when not in DEV (production)', async () => {
    vi.stubEnv('DEV', false);
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const mock = {
      status: 200,
      headers: { get: () => 'application/json' },
      text: () => Promise.resolve(JSON.stringify({ from: 'mock' })),
    };
    const res = await passthrough('kpis', mock as unknown as Response);
    expect(res.status).toBe(200);
    expect(fetchSpy).not.toHaveBeenCalled();
    const body = await res.json();
    expect(body).toEqual({ from: 'mock' });
  });

});
