export class ApiError extends Error {
  constructor(
    public override readonly message: string,
    public readonly status: number,
    public readonly url: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// Default to MSW (path /api intercepted by the dev service worker).
// The ApiStatusBadge component probes http://127.0.0.1:8000/health
// every 10 s and persists 'live' or 'mock' to localStorage. We mirror
// that decision here so requests to /api/admin/*, /api/pipeline, etc.
// hit the real backend when it's up, or fall back to MSW otherwise.
// VITE_API_BASE_URL still wins as a build-time override.
const STATIC_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined);
const LIVE_BASE_URL = 'http://127.0.0.1:8000/api';

function getBaseUrl(): string {
  /* v8 ignore next */
  if (STATIC_BASE_URL) return STATIC_BASE_URL;
  if (typeof localStorage !== 'undefined') {
    try {
      if (localStorage.getItem('algotrader.apiMode') === 'live') return LIVE_BASE_URL;
      /* v8 ignore next */
      if (localStorage.getItem('algotrader.apiMode') === 'mock') return LIVE_BASE_URL;
    } catch {
      /* v8 ignore next */
      /* ignore */
    }
  }
  return '/api';
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${getBaseUrl()}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new ApiError(
      `${res.status} ${res.statusText}${body ? `: ${body.slice(0, 200)}` : ''}`,
      res.status,
      url,
    );
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
