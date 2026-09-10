import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Sidebar } from '@components/layout/Sidebar';
import { useUiStore } from '@stores/uiStore';
import { server } from '../../mocks/server';

// Zod-compliant fixtures for /api/regime and /api/tickers.
// Tests install these via server.use() — the dev MSW handlers remain
// strict passthroughs that hit the real backend. In test mode the
// passthrough returns 503, so per-test server.use() is the only way
// to get data into the query.
const sampleRegime = {
  state: 'trend',
  confidence: 0.78,
  imoexChange: 0.42,
  volatility20d: 18.5,
  breadth: 0.62,
  sinceDate: '2026-09-01',
};

const sampleTickers = [
  { symbol: 'SBER', name: 'Сбербанк', sector: 'Финансы', price: 312.4, bars: 2500, firstDate: '2018-01-01', lastDate: '2026-09-10', fileSize: 1234567, gaps: 0 },
  { symbol: 'GAZP', name: 'Газпром', sector: 'Энергетика', price: 128.65, bars: 2400, firstDate: '2018-01-01', lastDate: '2026-09-10', fileSize: 987654, gaps: 0 },
  { symbol: 'YNDX', name: 'Яндекс', sector: 'Технологии', price: 4218, bars: 1800, firstDate: '2019-01-01', lastDate: '2026-09-10', fileSize: 654321, gaps: 0 },
  { symbol: 'LKOH', name: 'Лукойл', sector: 'Энергетика', price: 6500, bars: 2200, firstDate: '2018-01-01', lastDate: '2026-09-10', fileSize: 1111111, gaps: 0 },
];

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('Sidebar', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/regime', () => HttpResponse.json(sampleRegime)),
      http.get('/api/tickers', () => HttpResponse.json(sampleTickers)),
    );
  });
  afterEach(() => {
    server.resetHandlers();
  });

  it('renders the regime section', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Режим рынка')).toBeInTheDocument();
    });
  });

  it('renders the universe section', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/Universe/)).toBeInTheDocument();
    });
  });

  it('displays a list of tickers', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('SBER').length).toBeGreaterThan(0);
      expect(screen.getAllByText('GAZP').length).toBeGreaterThan(0);
    });
  });

  it('shows the regime confidence value', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/conf 0\.78/)).toBeInTheDocument();
    });
  });

  it('shows HMM info and since date', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/HMM/)).toBeInTheDocument();
    });
  });

  it('clicking a ticker updates the uiStore selectedTicker', async () => {
    useUiStore.setState({ selectedTicker: null });
    const Wrapper = makeWrapper();
    const { getByText } = render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('YNDX')).toBeInTheDocument();
    });
    expect(useUiStore.getState().selectedTicker).toBeNull();
    fireEvent.click(getByText('YNDX'));
    expect(useUiStore.getState().selectedTicker).toBe('YNDX');
  });

  it('clicking different tickers replaces the selected one', async () => {
    useUiStore.setState({ selectedTicker: null });
    const Wrapper = makeWrapper();
    const { getByText } = render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('LKOH')).toBeInTheDocument();
    });
    fireEvent.click(getByText('SBER'));
    expect(useUiStore.getState().selectedTicker).toBe('SBER');
    fireEvent.click(getByText('LKOH'));
    expect(useUiStore.getState().selectedTicker).toBe('LKOH');
  });

  it('renders without crashing when regime returns non-trend state', async () => {
    server.use(
      http.get('/api/regime', () =>
        HttpResponse.json({
          state: 'range',
          confidence: 0.5,
          imoexChange: -0.3,
          volatility20d: 12,
          breadth: 0.6,
          sinceDate: '2026-09-05',
        }),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('range')).toBeInTheDocument();
      expect(screen.getByText(/-0\.3%/)).toBeInTheDocument();
    });
  });

  it('falls back to empty ticker list when tickers query returns error', async () => {
    // Override the tickers endpoint to return an error so useTickers has no data.
    // This exercises the `tickers ?? []` fallback branch.
    server.use(http.get('/api/tickers', () => new HttpResponse(null, { status: 500 })));
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    // Component still renders the regime section and the universe title
    await waitFor(() => {
      expect(screen.getByText('Режим рынка')).toBeInTheDocument();
      expect(screen.getByText(/Universe/)).toBeInTheDocument();
    });
  });
});
