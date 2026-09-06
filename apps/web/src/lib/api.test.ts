import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from '@lib/api';

describe('ApiError', () => {
  it('is an instance of Error with all expected fields', () => {
    const err = new ApiError('boom', 500, '/api/x');
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.name).toBe('ApiError');
    expect(err.message).toBe('boom');
    expect(err.status).toBe(500);
    expect(err.url).toBe('/api/x');
  });
});

describe('api()', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns parsed JSON on 2xx response', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, value: 42 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const result = await api<{ ok: boolean; value: number }>('/data');
    expect(result).toEqual({ ok: true, value: 42 });
  });

  it('prepends /api base URL', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(new Response('{}', { status: 200 }));
    await api('/signals');
    expect(fetch).toHaveBeenCalledWith('/api/signals', expect.any(Object));
  });

  it('passes through method and body from init', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(new Response('{}', { status: 200 }));
    await api('/x', { method: 'POST', body: '{"a":1}' });
    const init = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]![1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(init.body).toBe('{"a":1}');
  });

  it('throws ApiError with status and url on 4xx', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('not found body', { status: 404, statusText: 'Not Found' }),
    );
    await expect(api('/missing')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
      url: '/api/missing',
      message: expect.stringContaining('404'),
    });
  });

  it('throws ApiError with status and url on 5xx', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('server error', { status: 500, statusText: 'Server Error' }),
    );
    try {
      await api('/fail');
      throw new Error('should have thrown');
    } catch (e) {
      const err = e as ApiError;
      expect(err.status).toBe(500);
      expect(err.url).toBe('/api/fail');
      expect(err.message).toContain('500');
      expect(err.message).toContain('Server Error');
    }
  });

  it('includes body excerpt in error message when body exists', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('{"detail":"oops"}', { status: 400, statusText: 'Bad Request' }),
    );
    try {
      await api('/fail');
    } catch (e) {
      expect((e as ApiError).message).toContain('oops');
    }
  });

  it('truncates very long error bodies to ~200 chars', async () => {
    const long = 'x'.repeat(500);
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(long, { status: 500, statusText: 'Server Error' }),
    );
    try {
      await api('/fail');
    } catch (e) {
      const msg = (e as ApiError).message;
      // "500 Server Error: <body up to 200 chars>"
      expect(msg.length).toBeLessThanOrEqual(250);
    }
  });

  it('handles error response with empty body', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('', { status: 503, statusText: 'Service Unavailable' }),
    );
    try {
      await api('/down');
    } catch (e) {
      const err = e as ApiError;
      expect(err.status).toBe(503);
      // No body excerpt after status text
      expect(err.message).toContain('503');
      expect(err.message).toContain('Service Unavailable');
    }
  });

  it('returns undefined for 204 No Content', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(new Response(null, { status: 204 }));
    const result = await api<undefined>('/delete');
    expect(result).toBeUndefined();
  });

  it('parses JSON body even when Content-Type header is missing', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('{"x":1}', { status: 200 }),
    );
    const result = await api<{ x: number }>('/raw');
    expect(result).toEqual({ x: 1 });
  });
});
