// passthrough(name, mock) — used by the dev-mode MSW handlers. Each
// handler first forwards the request to the real backend at
// http://127.0.0.1:8000/api/<name> and falls back to the hardcoded mock
// when the backend is unreachable (offline / blocked / wrong port). The
// UI shows real data whenever the backend is up, and stable mock data
// otherwise — no toggle, no per-user flag, no per-tab reload.

const LIVE_BASE = 'http://127.0.0.1:8000/api';

interface MockPayload<T> {
  data: T;
  meta?: Record<string, unknown>;
}

/**
 * Try the live backend at /api/<name>. On any error (network, timeout,
 * non-2xx) return the supplied mock wrapped in HttpResponse. Returns a
 * function so it can be plugged straight into an MSW handler closure.
 */
export async function passthrough<T>(
  name: string,
  mock: MockPayload<T> | T,
  timeoutMs = 1500,
): Promise<Response> {
  // In production / tests, MSW runs from the dev service worker only,
  // so skip the live probe entirely and return the mock.
  /* v8 ignore next */
  if (!import.meta.env.DEV) {
    const body = await (mock as unknown as Response).text();
    const status = (mock as unknown as Response).status;
    const ctype = (mock as unknown as Response).headers.get('content-type');
    return new Response(body, {
      status,
      headers: { 'content-type': ctype ?? 'application/json' },
    });
  }
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    const live = await fetch(`${LIVE_BASE}/${name}`, {
      signal: ctrl.signal,
      credentials: 'omit',
      headers: { Accept: 'application/json' },
    });
    clearTimeout(timer);
    if (live.ok) {
      const text = await live.text();
      return new Response(text, {
        status: 200,
        headers: { 'content-type': live.headers.get('content-type') ?? 'application/json' },
      });
    }
  } catch {
    /* backend offline / blocked / CORS — fall through to mock */
  }
  // `mock` is an HttpResponse-like object with .status and .text().
  const body = await (mock as unknown as Response).text();
  const status = (mock as unknown as Response).status;
  const ctype = (mock as unknown as Response).headers.get('content-type');
  return new Response(body, {
    status,
    headers: { 'content-type': ctype ?? 'application/json' },
  });
}
