// passthrough(endpoint) — used by the dev-mode MSW handlers. Each
// handler forwards the request to the real backend at
// http://127.0.0.1:8000/api/<endpoint>. On any error (network, timeout,
// non-2xx) we return an explicit 5xx with a JSON body explaining the
// endpoint name — never hardcoded mock data. The UI sees real data when
// the backend is up and an honest "no data" signal when it isn't.

// In dev, fetch /api/* on the same origin and let Vite proxy them to
// the backend at http://127.0.0.1:8000. Same-origin avoids the CORS
// trip a service-worker-initiated cross-origin fetch would otherwise
// trigger (browser blocks it before our 503 fallback can react).
const is_dev = typeof import.meta !== 'undefined' && import.meta.env?.DEV;
const LIVE_BASE = is_dev ? '/api' : 'http://127.0.0.1:8000/api';

/**
 * Forward the request to the live backend. On error return a
 * structured 5xx response so the UI can show "—" / "0" / "n/a"
 * without inventing values.
 *
 * @param endpoint Backend path relative to /api (e.g. 'kpis',
 *   'model/features', 'admin/backfill/status'). Slashes are preserved
 *   so the caller can address nested resources.
 * @param request Original Request from MSW (path, query, headers,
 *   body) — passed straight through so callers don't have to rebuild
 *   the URL.
 * @param timeoutMs Hard timeout for the live attempt.
 */
export async function passthrough(
  endpoint: string,
  request?: Request,
  timeoutMs = 5000,
): Promise<Response> {
  // DEV = vite dev mode (browser). In test mode (vitest+msw/node) the
  // passthrough is a no-op — calling fetch() from inside a MSW node
  // handler would re-trigger MSW and recurse forever. Tests that need
  // to assert against canned data install their own handlers via
  // server.use(). PROD is a static build where MSW is not loaded.
  /* v8 ignore next */
  if (!import.meta.env.DEV) {
    return new Response(JSON.stringify({ error: 'msw_disabled_in_test_or_prod' }), {
      status: 503,
      headers: { 'content-type': 'application/json' },
    });
  }

  const url = request ? new URL(request.url) : null;
  const target = `${LIVE_BASE}/${endpoint}${url?.search ?? ''}`;

  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    const live = await fetch(target, {
      method: request?.method ?? 'GET',
      headers: request?.headers ?? {},
      body:
        request && request.method !== 'GET' && request.method !== 'HEAD'
          ? await request.clone().arrayBuffer()
          : undefined,
      signal: ctrl.signal,
      credentials: 'omit',
    });
    clearTimeout(timer);

    if (live.ok) {
      const text = await live.text();
      return new Response(text, {
        status: live.status,
        headers: {
          'content-type': live.headers.get('content-type') ?? 'application/json',
        },
      });
    }
    // Non-2xx: wrap so the UI can render an honest empty state.
    return new Response(
      JSON.stringify({
        error: 'backend_error',
        endpoint,
        status: live.status,
      }),
      { status: 502, headers: { 'content-type': 'application/json' } },
    );
  } catch (err) {
    const aborted = err instanceof DOMException && err.name === 'AbortError';
    return new Response(
      JSON.stringify({
        error: aborted ? 'backend_timeout' : 'backend_unreachable',
        endpoint,
      }),
      {
        status: aborted ? 504 : 503,
        headers: { 'content-type': 'application/json' },
      },
    );
  }
}
