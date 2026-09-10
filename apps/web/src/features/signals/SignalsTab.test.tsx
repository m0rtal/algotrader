import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SignalsTab } from '@features/signals/SignalsTab';
import { useUiStore } from '@stores/uiStore';
import { server } from '../../mocks/server';

const seriesMock = { setData: vi.fn() };
const chartMock = {
  addSeries: vi.fn(() => seriesMock),
  remove: vi.fn(),
  applyOptions: vi.fn(),
  timeScale: () => ({ fitContent: vi.fn() }),
};
vi.mock('lightweight-charts', () => ({
  createChart: vi.fn(() => chartMock),
  AreaSeries: function AreaSeries() {},
}));

// Test fixtures for the MSW handlers. Tests install these via
// server.use() — the dev MSW handlers remain strict passthroughs that
// hit the real backend. This file intentionally does NOT mock @lib/api
// globally; that happens in src/test/setup.ts so other test files can
// opt-in per-test.
const sampleSignals = [
  { symbol: 'SBER', side: 'long', price: 312.4, forecast5d: 0.028, confidence: 0.72, strength: 0.6, regime: 'trend', volume: 5200, updatedAt: '2026-09-10T19:34:00Z' },
  { symbol: 'GAZP', side: 'short', price: 128.65, forecast5d: -0.019, confidence: 0.64, strength: 0.55, regime: 'range', volume: 8100, updatedAt: '2026-09-10T19:34:00Z' },
  { symbol: 'YNDX', side: 'hold', price: 4218, forecast5d: 0.034, confidence: 0.81, strength: 0.8, regime: 'trend', volume: 1200, updatedAt: '2026-09-10T19:34:00Z' },
];

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('SignalsTab', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/signals', () => HttpResponse.json(sampleSignals)),
    );
  });
  afterEach(() => {
    server.resetHandlers();
  });

  it('renders the table header', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Сигнал')).toBeInTheDocument();
      expect(screen.getByText('Прогноз 5д')).toBeInTheDocument();
    });
  });

  it('renders rows for each signal', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('SBER').length).toBeGreaterThan(0);
      expect(screen.getAllByText('GAZP').length).toBeGreaterThan(0);
    });
  });

  it('clicking a ticker symbol opens drill-down', async () => {
    useUiStore.setState({ selectedTicker: null });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('YNDX').length).toBeGreaterThan(0);
    });
    const yndxButtons = screen.getAllByText('YNDX');
    fireEvent.click(yndxButtons[0]!);
    expect(useUiStore.getState().selectedTicker).toBe('YNDX');
  });

  it('displays the long/short/hold badge with the right text', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('long').length).toBeGreaterThan(0);
      expect(screen.getAllByText('short').length).toBeGreaterThan(0);
      expect(screen.getAllByText('hold').length).toBeGreaterThan(0);
    });
  });

  it('renders the equity curve section title', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/Equity Curve/)).toBeInTheDocument();
    });
  });

  it('shows loading state when query is pending', () => {
    // The mock api() is reset between tests, so api('/signals') resolves
    // with undefined → TanStack Query stays in pending state.
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    expect(screen.getByText('Загрузка…')).toBeInTheDocument();
  });

  it('shows error state when query fails', async () => {
    server.use(http.get('/api/signals', () => new HttpResponse(null, { status: 500 })));
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Ошибка загрузки')).toBeInTheDocument();
    });
  });

  it('shows empty state when signal list is empty', async () => {
    server.use(http.get('/api/signals', () => HttpResponse.json([])));
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Нет сигналов')).toBeInTheDocument();
    });
  });
});
