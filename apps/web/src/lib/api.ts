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

const BASE_URL = '/api';

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${path}`;
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
