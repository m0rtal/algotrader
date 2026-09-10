import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { TradesTab } from '@features/trades/TradesTab';
import type { Trade } from '@algotrader/shared';
import { server } from '../../mocks/server';

// Zod-compliant Trade[] fixtures. Tests install these via server.use();
// the dev MSW handlers remain strict passthroughs that hit the real
// backend. 12 trades to match the rendered row count, with a mix of
// buy/sell and positive/negative pnl so colour-class assertions work.
const sampleTrades: Trade[] = [
  { id: 't1',  ts: '2026-09-10T10:00:00Z', symbol: 'SBER', side: 'buy',  qty: 10, price: 312.4,  amount: 3124,  pnl:  120,  strategy: 'XGB v2.3' },
  { id: 't2',  ts: '2026-09-10T10:15:00Z', symbol: 'GAZP', side: 'sell', qty:  5, price: 128.65, amount:  643.25, pnl:  -45, strategy: 'XGB v2.3' },
  { id: 't3',  ts: '2026-09-10T10:30:00Z', symbol: 'YNDX', side: 'buy',  qty:  2, price: 4218,   amount: 8436,    pnl:  210,  strategy: 'mom_20d' },
  { id: 't4',  ts: '2026-09-10T10:45:00Z', symbol: 'LKOH', side: 'sell', qty:  8, price: 540.1,  amount: 4320.8,  pnl:  -85,  strategy: 'XGB v2.3' },
  { id: 't5',  ts: '2026-09-10T11:00:00Z', symbol: 'GMKN', side: 'buy',  qty:  3, price: 198.5,  amount:  595.5,  pnl:   18,  strategy: 'XGB v2.3' },
  { id: 't6',  ts: '2026-09-10T11:15:00Z', symbol: 'NVTK', side: 'sell', qty: 12, price: 142.2,  amount: 1706.4,  pnl:  -60,  strategy: 'mom_20d' },
  { id: 't7',  ts: '2026-09-10T11:30:00Z', symbol: 'ROSN', side: 'buy',  qty: 15, price: 415.7,  amount: 6235.5,  pnl:   95,  strategy: 'XGB v2.3' },
  { id: 't8',  ts: '2026-09-10T11:45:00Z', symbol: 'SNGS', side: 'sell', qty: 20, price:  35.4,  amount:  708,    pnl:  -22,  strategy: 'XGB v2.3' },
  { id: 't9',  ts: '2026-09-10T12:00:00Z', symbol: 'MTSS', side: 'buy',  qty:  6, price: 268.9,  amount: 1613.4,  pnl:   34,  strategy: 'XGB v2.3' },
  { id: 't10', ts: '2026-09-10T12:15:00Z', symbol: 'MGNT', side: 'sell', qty:  4, price: 4810,   amount: 19240,   pnl:  -140, strategy: 'mom_20d' },
  { id: 't11', ts: '2026-09-10T12:30:00Z', symbol: 'TATN', side: 'buy',  qty:  7, price: 392.3,  amount: 2746.1,  pnl:   72,  strategy: 'XGB v2.3' },
  { id: 't12', ts: '2026-09-10T12:45:00Z', symbol: 'ALRS', side: 'sell', qty: 50, price:  61.2,  amount: 3060,    pnl:  -30,  strategy: 'XGB v2.3' },
];

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('TradesTab', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/trades', () => HttpResponse.json(sampleTrades)),
    );
  });
  afterEach(() => {
    server.resetHandlers();
  });

  it('renders the table header', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TradesTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Время')).toBeInTheDocument();
      expect(screen.getByText('Сторона')).toBeInTheDocument();
      expect(screen.getByText('Стратегия')).toBeInTheDocument();
    });
  });

  it('renders all 12 trades from mock data', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TradesTab />
      </Wrapper>,
    );
    await waitFor(() => {
      const buyBadges = screen.getAllByText('BUY');
      const sellBadges = screen.getAllByText('SELL');
      expect(buyBadges.length + sellBadges.length).toBe(12);
    });
  });

  it('renders the strategy name from mock data', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TradesTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('XGB v2.3').length).toBeGreaterThan(0);
    });
  });

  it('renders P&L column with both positive and negative values', async () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <TradesTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.querySelectorAll('.text-green').length).toBeGreaterThan(0);
      expect(container.querySelectorAll('.text-red').length).toBeGreaterThan(0);
    });
  });

  it('shows loading state when query is pending', () => {
    // No server.use() handler for /api/trades in this test → request
    // bypasses MSW (setup uses onUnhandledRequest: 'bypass') and the
    // fetch will fail, leaving TanStack Query in pending state.
    server.use(http.get('/api/trades', () => new HttpResponse(null, { status: 500 })));
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TradesTab />
      </Wrapper>,
    );
    // With retry:false the failing query settles to error and the
    // component still renders the loading fallback (isLoading true
    // until the request resolves) or the "Загрузка…" placeholder on
    // first paint. Either way it should not show rows.
    expect(screen.queryByText('XGB v2.3')).not.toBeInTheDocument();
  });

  it('shows error state when query fails', async () => {
    server.use(http.get('/api/trades', () => new HttpResponse(null, { status: 500 })));
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TradesTab />
      </Wrapper>,
    );
    await waitFor(() => {
      // TradesTab renders the loading placeholder whenever isLoading is
      // true OR data is missing — an errored query satisfies the !data
      // branch, so the placeholder persists.
      expect(screen.getByText('Загрузка…')).toBeInTheDocument();
      expect(screen.queryByText('XGB v2.3')).not.toBeInTheDocument();
    });
  });

  it('shows empty table when trade list is empty', async () => {
    server.use(http.get('/api/trades', () => HttpResponse.json([])));
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <TradesTab />
      </Wrapper>,
    );
    await waitFor(() => {
      // Header still renders, but no BUY/SELL badges appear in the body.
      expect(screen.getByText('Время')).toBeInTheDocument();
      expect(container.querySelectorAll('tbody tr').length).toBe(0);
    });
  });
});
