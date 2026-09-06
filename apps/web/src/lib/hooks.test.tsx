import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  useBars,
  useFolds,
  useKpis,
  useModel,
  useModelFeatures,
  usePipeline,
  usePortfolio,
  useRegime,
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
});
