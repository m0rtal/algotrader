import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TickerDrilldown } from '@components/charts/TickerDrilldown';
import type { BarsSeries, Ticker } from '@algotrader/shared';
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

// Zod-compliant fixtures. Tickers and BarsSeries match
// TickerSchema / BarsSeriesSchema in @algotrader/shared so the
// queryFn's `schema.parse()` does not strip or coerce them.
const sampleTickers: Ticker[] = [
  {
    symbol: 'SBER',
    name: 'Сбер',
    sector: 'Banks',
    price: 312.4,
    bars: 30,
    firstDate: '2024-08-01',
    lastDate: '2024-09-09',
    fileSize: 2048,
    gaps: 0,
  },
  {
    symbol: 'GAZP',
    name: 'Газпром',
    sector: 'Energy',
    price: 128.65,
    bars: 30,
    firstDate: '2024-08-01',
    lastDate: '2024-09-09',
    fileSize: 2048,
    gaps: 0,
  },
  {
    symbol: 'YNDX',
    name: 'Яндекс',
    sector: 'IT',
    price: 4218,
    bars: 30,
    firstDate: '2024-08-01',
    lastDate: '2024-09-09',
    fileSize: 2048,
    gaps: 0,
  },
];

const sampleBars: BarsSeries = {
  symbol: 'SBER',
  count: 30,
  first: '2024-08-01',
  last: '2024-09-09',
  bars: Array.from({ length: 30 }, (_, i) => ({
    ts: `2024-08-${String(i + 1).padStart(2, '0')}`,
    open: 300 + i,
    high: 305 + i,
    low: 295 + i,
    close: 302 + i,
    volume: 1000 + i * 10,
  })),
};

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('TickerDrilldown', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/tickers', () => HttpResponse.json(sampleTickers)),
      http.get('/api/bars/:symbol', () => HttpResponse.json(sampleBars)),
    );
  });
  afterEach(() => {
    server.resetHandlers();
  });

  it('renders nothing when no ticker is selected', () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    expect(container.querySelector('.fixed')).toBeNull();
  });

  it('renders the modal when a ticker is selected', async () => {
    useUiStore.setState({ selectedTicker: 'SBER' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    // wait for ticker data to load
    await waitFor(() => {
      expect(screen.getByText(/data\/bars\/SBER\.parquet/)).toBeInTheDocument();
    });
  });

  it('shows the ticker name in the header', async () => {
    useUiStore.setState({ selectedTicker: 'GAZP' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/Газпром/)).toBeInTheDocument();
    });
  });

  it('displays the source path', async () => {
    useUiStore.setState({ selectedTicker: 'YNDX' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/data\/bars\/YNDX\.parquet/)).toBeInTheDocument();
    });
  });

  it('displays the MOEX ISS source', async () => {
    useUiStore.setState({ selectedTicker: 'YNDX' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/MOEX ISS/)).toBeInTheDocument();
    });
  });

  it('shows the close button', async () => {
    useUiStore.setState({ selectedTicker: 'SBER' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('✕')).toBeInTheDocument();
    });
  });

  it('close button clears the selected ticker', async () => {
    useUiStore.setState({ selectedTicker: 'SBER' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('✕')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('✕'));
    expect(useUiStore.getState().selectedTicker).toBeNull();
  });

  it('renders sector label', async () => {
    useUiStore.setState({ selectedTicker: 'SBER' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Banks')).toBeInTheDocument();
    });
  });

  it('displays the bar count from API', async () => {
    useUiStore.setState({ selectedTicker: 'SBER' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      // mock returns 30 bars
      expect(screen.getByText('30')).toBeInTheDocument();
    });
  });
});