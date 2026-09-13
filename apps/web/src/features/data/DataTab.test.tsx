import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { DataTab } from '@features/data/DataTab';
import { server } from '../../mocks/server';

// Mock the UI store at module level so DataTab's destructure of
// `openTicker` resolves in every test. The mock implements the same
// selector-style API as the real store, plus a `calls` log so the
// row-click test can assert what was invoked.
const openTickerMock = vi.fn();
vi.mock('@stores/uiStore', () => ({
  useUiStore: (sel: (s: unknown) => unknown) =>
    sel({
      activeTab: 'data',
      selectedTicker: null,
      sidebarOpen: false,
      railOpen: false,
      openTicker: (...args: unknown[]) => {
        openTickerMock(...args);
      },
      closeTicker: () => {},
      toggleSidebar: () => {},
      toggleRail: () => {},
      closeSidebars: () => {},
      setActiveTab: () => {},
    }),
}));

// ─── Default fixtures ────────────────────────────────────────────────
// Same shapes as the real backend on a healthy system.

const defaultStatus = {
  state: 'idle',
  run_id: null,
  tickers_total: 0,
  tickers_done: 0,
  total_bars: 2_361_394,
  last_run: null,
};

const defaultPending = {
  new: 3,
  stale: 1,
  up_to_date: 12,
  error: 0,
  total: 16,
};

const sampleTickers = [
  {
    symbol: 'SBER',
    name: 'Sberbank',
    sector: 'Financials',
    price: 312.4,
    bars: 1500,
    firstDate: '2021-09-01',
    lastDate: '2026-09-13',
    fileSize: 102400,
    gaps: 2,
  },
  {
    symbol: 'GAZP',
    name: 'Gazprom',
    sector: 'Energy',
    price: 178.2,
    bars: 1500,
    firstDate: '2021-09-01',
    lastDate: '2026-09-13',
    fileSize: 98000,
    gaps: 0,
  },
];

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  // displayName so any lint warning about anonymous component
  // surfaces a useful identifier.
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.displayName = 'QueryClientWrapper';
  return Wrapper;
}

const Wrap = makeWrapper();

describe('DataTab', () => {
  beforeEach(() => {
    // Reset server handlers so leftover handlers from the previous
    // test (e.g. the running-status override) do not leak into the
    // current test's MSW responses.
    server.resetHandlers();
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json(defaultStatus),
      ),
      http.get('/api/admin/backfill/pending', () =>
        HttpResponse.json(defaultPending),
      ),
      http.get('/api/tickers', () => HttpResponse.json(sampleTickers)),
      http.post('/api/admin/backfill/stop', () =>
        HttpResponse.json({ ok: true }),
      ),
      http.post('/api/admin/backfill/force-reset', () =>
        HttpResponse.json({ deleted_rows: 0 }),
      ),
    );
  });
  afterEach(() => {
    server.resetHandlers();
  });

  it('renders the Данные heading', () => {
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    expect(screen.getByRole('heading', { name: 'Данные' })).toBeInTheDocument();
  });

  it('reads total_bars from the status endpoint, not from the ticker aggregate', async () => {
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    // status.total_bars = 2_361_394 (server-side canonical). The
    // ticker list has bars=1500 each, sum = 3_000. The page MUST
    // not re-aggregate from tickers — show the server value.
    // The `\d[\u00a0\d]` regex tolerates whatever whitespace
    // toLocaleString('ru') emits between digit groups.
    const barsCard = await screen.findByText(/2[\u00a0 ]361[\u00a0 ]394/, undefined, { timeout: 5000 });
    expect(barsCard).toBeInTheDocument();
  });

  it('renders pending counters from usePendingCount', async () => {
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    await waitFor(() => {
      expect(screen.getByText('Новые')).toBeInTheDocument();
    });
    expect(screen.getByText(/Устаревшие/)).toBeInTheDocument();
    expect(screen.getByText('С ошибками')).toBeInTheDocument();
  });

  it('renders the three action buttons (idle state)', async () => {
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    expect(
      await screen.findByRole('button', { name: /Запустить бэкфилл/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Остановить/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Сбросить метаданные/i }),
    ).toBeInTheDocument();
  });

  it('does not render a progress bar when idle (tickers_total = 0)', async () => {
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    await waitFor(() => {
      expect(screen.queryByRole('progressbar')).toBeNull();
    });
  });

  it('renders a progress bar with the right aria-valuenow while running', async () => {
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json({
          ...defaultStatus,
          state: 'running',
          tickers_total: 100,
          tickers_done: 30,
        }),
      ),
    );
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    const bar = await screen.findByRole('progressbar');
    expect(bar.getAttribute('aria-valuenow')).toBe('30');
  });

  it('routes ticker row clicks to openTicker', async () => {
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    const sberRow = await screen.findByText(/SBER/);
    fireEvent.click(sberRow);
    expect(openTickerMock).toHaveBeenCalledWith('SBER');
  });
});
