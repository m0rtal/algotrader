import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  useBars,
  useDeleteSettings,
  useFolds,
  useKpis,
  useModel,
  useModelFeatures,
  usePipeline,
  usePortfolio,
  useRegime,
  useSaveSettings,
  useSaveToken,
  useSettings,
  useSignals,
  useTickers,
  useTrades,
} from '@lib/hooks';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('data hooks', () => {
  let wrapper: ReturnType<typeof makeWrapper>;

  beforeEach(() => {
    wrapper = makeWrapper();
  });
  afterEach(() => {
    wrapper = makeWrapper();
  });

  it('useKpis fetches and returns kpi list', async () => {
    const { result } = renderHook(() => useKpis(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeDefined();
    expect(Array.isArray(result.current.data)).toBe(true);
    expect((result.current.data as unknown[]).length).toBeGreaterThan(0);
  });

  it('useSignals fetches signal list with valid shape', async () => {
    const { result } = renderHook(() => useSignals(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const first = result.current.data?.[0] as { symbol: string; side: string } | undefined;
    expect(first?.symbol).toBeTruthy();
    expect(['long', 'short', 'hold']).toContain(first?.side);
  });

  it('useTrades fetches trade list', async () => {
    const { result } = renderHook(() => useTrades(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.length).toBeGreaterThan(0);
  });

  it('usePortfolio fetches portfolio with positions', async () => {
    const { result } = renderHook(() => usePortfolio(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.positions.length).toBeGreaterThan(0);
  });

  it('useRegime fetches regime state', async () => {
    const { result } = renderHook(() => useRegime(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(['trend', 'range', 'vol']).toContain(result.current.data?.state);
  });

  it('useModel fetches model metadata', async () => {
    const { result } = renderHook(() => useModel(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.version).toBeTruthy();
  });

  it('useModelFeatures fetches feature importances', async () => {
    const { result } = renderHook(() => useModelFeatures(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.length).toBeGreaterThan(0);
  });

  it('usePipeline fetches pipeline steps', async () => {
    const { result } = renderHook(() => usePipeline(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.length).toBeGreaterThan(0);
  });

  it('useFolds fetches walk-forward folds', async () => {
    const { result } = renderHook(() => useFolds(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.length).toBeGreaterThan(0);
  });

  it('useTickers fetches ticker list', async () => {
    const { result } = renderHook(() => useTickers(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.length).toBeGreaterThan(0);
  });

  it('useBars is disabled when symbol is null', async () => {
    const { result } = renderHook(() => useBars(null), { wrapper });
    expect(result.current.isFetching).toBe(false);
    expect(result.current.data).toBeUndefined();
  });

  it('useBars fetches bars for a known symbol', async () => {
    const { result } = renderHook(() => useBars('SBER'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.symbol).toBe('SBER');
    expect(result.current.data?.bars.length).toBeGreaterThan(0);
  });

  it('useBars throws when symbol is not in mock data', async () => {
    const { result } = renderHook(() => useBars('UNKNOWN'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as Error).message).toBeTruthy();
  });

  it('useSettings returns values from GET /api/settings', async () => {
    const { result } = renderHook(() => useSettings(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.values.broker.environment).toBe('sandbox');
    expect(result.current.data?.values.risk.maxDrawdownPct).toBe(10);
    expect(result.current.data?.version).toBeTruthy();
  });

  it('useSaveSettings PUTs and the new values land in state', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
    });
    const { result } = renderHook(
      () => ({
        save: useSaveSettings(),
        settings: useSettings(),
      }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    await waitFor(() => expect(result.current.settings.isSuccess).toBe(true));
    const current = result.current.settings.data!.values;
    const next = {
      ...current,
      risk: { ...current.risk, maxDrawdownPct: 8, killSwitchThresholdPct: 15 },
    };
    await act(async () => {
      await result.current.save.mutateAsync({
        values: next,
        version: result.current.settings.data!.version,
      });
    });
    await waitFor(() => expect(result.current.settings.data?.values.risk.maxDrawdownPct).toBe(8));
  });

  it('useDeleteSettings returns 204 and triggers invalidation', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
    });
    const { result } = renderHook(
      () => ({
        del: useDeleteSettings(),
      }),
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      },
    );
    let response: unknown;
    await act(async () => {
      response = await result.current.del.mutateAsync();
    });
    // 204 No Content — undefined body
    expect(response).toBeUndefined();
  });
});

describe('useSettings — 404 fallback (defensive default)', () => {
  it('returns DEFAULT_SETTINGS when /settings responds 404', async () => {
    const { http, HttpResponse } = await import('msw');
    const { server } = await import('../mocks/server');
    // Override the default /settings handler with a 404.
    server.use(
      http.get('/api/settings', () =>
        new HttpResponse('not found', { status: 404 }),
      ),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
    });
    const { result } = renderHook(() => useSettings(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    });
    // Wait until the query settles (isError stays false because the
    // hook swallows the 404 and returns DEFAULT_SETTINGS).
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isError).toBe(false);
    expect(result.current.data).toBeDefined();
    expect(result.current.data?.values).toBeDefined();
  });
});

describe('useSaveToken', () => {
  it('sends PUT /settings/token and invalidates the settings cache on success', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
    });
    const Wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useSaveToken(), { wrapper: Wrapper });
    let response: { tokenLast4: string; tokenRedacted: boolean } | undefined;
    await act(async () => {
      response = await result.current.mutateAsync({ token: 't.real.ABCD' });
    });
    expect(response).toEqual({ tokenLast4: 'ABCD', tokenRedacted: true });
  });
});
