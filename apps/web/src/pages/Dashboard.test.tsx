import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Dashboard } from '@pages/Dashboard';
import { server } from '../mocks/server';

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

// MSW handlers are strict passthroughs in this codebase; every test
// installs canned data via server.use(). Fixtures below are minimal
// and Zod-compliant (KpiSchema, SignalSchema, TradeSchema,
// PortfolioSchema, RegimeSchema, ModelSchema, FeatureImportanceSchema,
// PipelineStepSchema, TickerSchema) and the inline backfill status /
// pending shapes used by features/backfill/BackfillTab.tsx.
//
// The Dashboard renders the Signals tab by default (uiStore's active
// tab); the Backfill tab — which opens an EventSource on mount — is
// only mounted if a test clicks it, so we don't need an EventSource
// stub for the assertions in this file.

const sampleKpis = [
  { label: 'Equity', value: '1.00M', tone: 'neutral' },
  { label: 'P&L', value: '+1.2%', tone: 'pos' },
];

const sampleSignals = [
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
];

const sampleTrades = [
  {
    id: 't1',
    ts: '2026-09-10T19:34:00Z',
    symbol: 'SBER',
    side: 'buy',
    qty: 10,
    price: 312.4,
    amount: 3124,
    pnl: null,
    strategy: 'ml',
  },
];

const samplePortfolio = {
  cash: 100000,
  invested: 50000,
  total: 150000,
  longCount: 2,
  shortCount: 0,
  grossExposure: 50000,
  netExposure: 50000,
  positions: [
    { symbol: 'SBER', side: 'long', qty: 10, avgPrice: 310, price: 312.4, value: 3124, weight: 0.02, pnl: 24 },
  ],
};

const sampleRegime = {
  state: 'trend',
  confidence: 0.7,
  imoexChange: 0.012,
  volatility20d: 0.18,
  breadth: 0.6,
  sinceDate: '2026-08-01',
};

const sampleModel = {
  version: 'v2.3',
  trainWindowMonths: 24,
  trainStart: '2024-01-01',
  trainEnd: '2025-12-31',
  oosAccuracy: 0.58,
  oosSharpe: 1.4,
  ic: 0.07,
  lastTrainDate: '2026-01-15',
  nextTrainDate: '2026-02-15',
};

const sampleModelFeatures = [
  { name: 'mom_20d', importance: 0.32 },
  { name: 'rsi_14', importance: 0.18 },
];

const samplePipeline = [
  { name: 'ingest', status: 'ok' as const },
  { name: 'features', status: 'ok' as const },
  { name: 'train', status: 'idle' as const, detail: 'scheduled' },
];

const sampleTickers = [
  { symbol: 'SBER', name: 'Sberbank', sector: 'Financials', price: 312.4, bars: 1000, firstDate: '2018-01-01', lastDate: '2026-09-10', fileSize: 102400, gaps: 0 },
];

const sampleBackfillPending = { new: 0, stale: 0, up_to_date: 1, error: 0, total: 1 };

const sampleBackfillStatus = {
  state: 'idle',
  run_id: null,
  tickers_done: 0,
  tickers_total: 0,
  total_bars: 1000,
  last_run: null,
};

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('Dashboard', () => {
  beforeEach(() => {
    chartMock.addSeries.mockClear();
    chartMock.remove.mockClear();
    chartMock.applyOptions.mockClear();
    seriesMock.setData.mockClear();
    server.use(
      http.get('/api/kpis', () => HttpResponse.json(sampleKpis)),
      http.get('/api/signals', () => HttpResponse.json(sampleSignals)),
      http.get('/api/trades', () => HttpResponse.json(sampleTrades)),
      http.get('/api/portfolio', () => HttpResponse.json(samplePortfolio)),
      http.get('/api/regime', () => HttpResponse.json(sampleRegime)),
      http.get('/api/model', () => HttpResponse.json(sampleModel)),
      http.get('/api/model/features', () => HttpResponse.json(sampleModelFeatures)),
      http.get('/api/pipeline', () => HttpResponse.json(samplePipeline)),
      http.get('/api/tickers', () => HttpResponse.json(sampleTickers)),
      http.get('/api/admin/backfill/pending', () => HttpResponse.json(sampleBackfillPending)),
      http.get('/api/admin/backfill/status', () => HttpResponse.json(sampleBackfillStatus)),
    );
  });
  afterEach(() => {
    server.resetHandlers();
  });

  it('renders the 5 tab buttons', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Сигналы' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Сделки' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Портфель' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Бэктест' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Бары' })).toBeInTheDocument();
    });
  });

  it('shows the Signals tab content by default', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Equity')).toBeInTheDocument();
      expect(screen.getByText('Сигнал')).toBeInTheDocument();
    });
  });

  it('clicking the Trades tab shows the trades content', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Сделки' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сделки' }));
    await waitFor(() => {
      expect(screen.getByText('Время')).toBeInTheDocument();
    });
  });

  it('clicking the Portfolio tab shows positions', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Портфель' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Портфель' }));
    await waitFor(() => {
      expect(screen.getByText('Свободно')).toBeInTheDocument();
    });
  });

  it('clicking the Backtest tab shows folds', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Бэктест' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Бэктест' }));
    await waitFor(() => {
      expect(screen.getByText('OOS Sharpe')).toBeInTheDocument();
    });
  });

  it('clicking the Storage tab shows bars table', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Бары' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Бары' }));
    await waitFor(() => {
      expect(screen.getByText('Всего баров')).toBeInTheDocument();
    });
  });

  it('renders the topbar and sidebar', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('ALGOTRADER')).toBeInTheDocument();
      expect(screen.getByText('Режим рынка')).toBeInTheDocument();
    });
  });

  it('renders the right rail with model and pipeline', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('ML Model')).toBeInTheDocument();
      expect(screen.getByText('Pipeline status')).toBeInTheDocument();
    });
  });
});
