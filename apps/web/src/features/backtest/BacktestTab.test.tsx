import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BacktestTab } from '@features/backtest/BacktestTab';
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

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const sampleFolds = [
  { id: 1, trainStart: '2023-01-01', trainEnd: '2024-06-30', testStart: '2024-07-01', testEnd: '2024-12-31', sharpe: 1.42, cagr: 0.18, winRate: 0.58, trades: 87, maxDd: -0.062 },
  { id: 2, trainStart: '2023-07-01', trainEnd: '2024-12-31', testStart: '2025-01-01', testEnd: '2025-06-30', sharpe: 1.65, cagr: 0.21, winRate: 0.62, trades: 92, maxDd: -0.048 },
  { id: 3, trainStart: '2024-01-01', trainEnd: '2025-06-30', testStart: '2025-07-01', testEnd: '2025-12-31', sharpe: 1.28, cagr: 0.14, winRate: 0.55, trades: 78, maxDd: -0.071 },
  { id: 4, trainStart: '2024-07-01', trainEnd: '2025-12-31', testStart: '2026-01-01', testEnd: '2026-06-30', sharpe: 1.84, cagr: 0.24, winRate: 0.66, trades: 105, maxDd: -0.041 },
];

describe('BacktestTab', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/backtest/folds', () => HttpResponse.json(sampleFolds)),
    );
  });
  afterEach(() => server.resetHandlers());

  it('renders the 5 summary cards', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BacktestTab />
      </Wrapper>,
    );
    expect(await screen.findByText('OOS Sharpe', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getAllByText('CAGR').length).toBeGreaterThan(0);
    expect(screen.getByText('Win Rate')).toBeInTheDocument();
    expect(screen.getByText('Profit Factor')).toBeInTheDocument();
    expect(screen.getAllByText('Max DD').length).toBeGreaterThan(0);
  });

  it('renders the walk-forward folds table', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BacktestTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Walk-Forward Folds')).toBeInTheDocument();
      expect(screen.getAllByText('Sharpe').length).toBeGreaterThan(0);
    });
  });

  it('renders 4 fold rows', async () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <BacktestTab />
      </Wrapper>,
    );
    await waitFor(() => {
      const tbody = container.querySelector('tbody');
      expect(tbody?.querySelectorAll('tr').length).toBe(4);
    });
  });

  it('shows Equity vs IMOEX section title', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BacktestTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Equity vs IMOEX')).toBeInTheDocument();
    });
  });
});
