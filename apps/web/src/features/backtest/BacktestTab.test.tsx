import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { BacktestTab } from '@features/backtest/BacktestTab';

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

describe('BacktestTab', () => {
  it('renders the 5 summary cards', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BacktestTab />
      </Wrapper>,
    );
    expect(await screen.findByText('OOS Sharpe', {}, { timeout: 5000 })).toBeInTheDocument();
    // CAGR appears as both a card label and a column header; use getAllByText
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
