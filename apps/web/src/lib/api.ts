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

// All traffic goes through /api which is intercepted by the MSW service
// worker in dev mode. The MSW handlers themselves forward to the real
// backend (http://127.0.0.1:8000/api) and fall back to hardcoded mocks
// when the backend is unreachable. That gives the UI real data when
// the backend is up, and stable mock data when it isn't — without any
// per-user flag, per-tab reload, or "live/mock" toggle.
//
// VITE_API_BASE_URL still wins as a build-time override (e.g. for the
// staging build, where MSW is not loaded at all).
const STATIC_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined);

export function getBaseUrl(): string {
  /* v8 ignore next */
  if (STATIC_BASE_URL) return STATIC_BASE_URL;
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
