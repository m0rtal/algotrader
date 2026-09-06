import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

describe('api base URL', () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({}),
    text: async () => '',
  });

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockClear();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('defaults to /api when no env var set', async () => {
    // Do NOT stub env — leave undefined to exercise default branch.
    const { api } = await import('@lib/api');
    await api('/test-default');
    expect(fetchMock).toHaveBeenCalledWith('/api/test-default', expect.any(Object));
  });

  it('uses VITE_API_BASE_URL when set', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'http://127.0.0.1:8000/api');
    const { api } = await import('@lib/api');
    await api('/settings');
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/settings',
      expect.any(Object),
    );
  });
});
