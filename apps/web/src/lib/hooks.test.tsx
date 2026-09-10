import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  useBars,
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
import { server } from '../mocks/server';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('data hooks (against MSW handlers returning canned data)', () => {
  let wrapper: ReturnType<typeof makeWrapper>;

  beforeEach(() => {
    wrapper = makeWrapper();
    server.use(
      http.get('/api/kpis', () =>
        HttpResponse.json([
          { label: 'Equity', value: '1 124 380 ₽', sub: '+12 438 ₽ · +1.12%' },
        ]),
      ),
      http.get('/api/signals', () =>
        HttpResponse.json([
          {
            symbol: 'SBER',
            side: 'long',
            price: 312.4,
            forecast5d: 0.028,
            confidence: 0.72,
            strength: 0.6,
            regime: 'trend',
            volume: 5200,
            updatedAt: '2026-09-10T19:34:00Z',
          },
        ]),
      ),
      http.get('/api/trades', () =>
        HttpResponse.json([
          {
            id: 't1',
            symbol: 'SBER',
            side: 'buy',
            qty: 10,
            price: 312.4,
            amount: 3124,
            pnl: null,
            strategy: 'mom_20d',
            ts: '2026-09-10T19:34:00Z',
          },
        ]),
      ),
      http.get('/api/portfolio', () =>
        HttpResponse.json({
          cash: 0,
          invested: 1124380,
          total: 1124380,
          longCount: 1,
          shortCount: 0,
          grossExposure: 1.0,
          netExposure: 1.0,
          positions: [
            {
              symbol: 'SBER',
              side: 'long',
              qty: 10,
              avgPrice: 300,
              price: 312.4,
              value: 3124,
              weight: 0.0028,
              pnl: 124,
            },
          ],
        }),
      ),
      http.get('/api/regime', () =>
        HttpResponse.json({
          state: 'trend',
          confidence: 0.78,
          imoexChange: 0.0042,
          volatility20d: 14.2,
          breadth: 0.71,
          sinceDate: '2026-09-04',
        }),
      ),
      http.get('/api/model', () =>
        HttpResponse.json({
          version: 'v2.3',
          trainWindowMonths: 24,
          trainStart: '2024-09',
          trainEnd: '2026-08',
          oosAccuracy: 0.572,
          oosSharpe: 1.84,
          ic: 0.081,
          lastTrainDate: '2026-09-01',
          nextTrainDate: '2026-10-01',
        }),
      ),
      http.get('/api/model/features', () =>
        HttpResponse.json([{ name: 'mom_20d', importance: 0.088 }]),
      ),
      http.get('/api/pipeline', () =>
        HttpResponse.json([{ name: 'Universe', status: 'ok' }]),
      ),
      http.get('/api/tickers', () =>
        HttpResponse.json([
          {
            symbol: 'SBER',
            name: 'Сбер Банк',
            sector: 'Финансы',
            price: 312.4,
            bars: 1,
            firstDate: '2026-09-10',
            lastDate: '2026-09-10',
            fileSize: 1024,
            gaps: 0,
          },
        ]),
      ),
      http.get('/api/bars/:symbol', ({ params }) => {
        if (params.symbol === 'SBER') {
          return HttpResponse.json({
            symbol: 'SBER',
            count: 1,
            first: '2026-09-10',
            last: '2026-09-10',
            bars: [{ ts: '2026-09-10', open: 312.4, high: 312.4, low: 312.4, close: 312.4, volume: 1000 }],
          });
        }
        return HttpResponse.json({ error: 'not_found' }, { status: 404 });
      }),
    );
  });
  afterEach(() => {
    server.resetHandlers();
    wrapper = makeWrapper();
  });

  it('useKpis fetches and returns kpi list', async () => {
    const { result } = renderHook(() => useKpis(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeDefined();
  });

  it('useSignals fetches signal list with valid shape', async () => {
    const { result } = renderHook(() => useSignals(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const first = result.current.data?.[0] as { symbol: string; side: string } | undefined;
    expect(first?.symbol).toBe('SBER');
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

  it('useBars surfaces an error when symbol is not in the backend', async () => {
    const { result } = renderHook(() => useBars('UNKNOWN'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as Error).message).toBeTruthy();
  });
});
