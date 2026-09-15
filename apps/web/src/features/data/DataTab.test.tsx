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
  {
    // NEW bond: short span (2025-01-01..2026-09-13 = 622 days), no gaps.
    // Used by the completeness test to force the per-ticker average
    // to differ from the (span-gaps)/span formula:
    //   sum formula: (1838 - (2+0+0)) / 1838 = 99.89% (coincidentally right)
    //   avg formula: mean(99.89%, 100%, 100%) = 99.96% — same
    // To make the bug visible we need gaps > 1 per ticker:
    symbol: 'BOND',
    name: 'Test bond',
    sector: '',
    price: 0,
    bars: 1500,
    firstDate: '2021-09-01',
    lastDate: '2026-09-13',
    fileSize: 0,
    gaps: 50,
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

  it('renders completeness as per-ticker average, not sum across tickers', async () => {
    /**
     * Regression: completeness formula was
     *   (global_span - sum_gaps) / global_span
     * which double-counted gaps across tickers and read 0% when sum
     * exceeded the global span (e.g. 73016 gaps vs 1861 days).
     *
     * Correct metric: average of (ticker_span - ticker_gaps) / ticker_span.
     *
     * Sample (after fix to make bug visible):
     *   SBER (1838 days span, 2 gaps)  pct = 99.89%
     *   GAZP (1838 days span, 0 gaps)  pct = 100.00%
     *   BOND (1838 days span, 50 gaps) pct = 97.28%
     *   sum formula: (1838 - 52)/1838 = 97.17% → "97.2%"  ← WRONG
     *   avg formula: mean(99.89, 100, 97.28) = 99.06% → "99.1%"  ← correct
     */
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    const value = await screen.findByText(/99[.,]1\s*%/);
    expect(value).toBeInTheDocument();
    // Belt-and-suspenders: must NOT show the sum formula's wrong answer.
    expect(screen.queryByText(/97[.,]2\s*%/)).toBeNull();
  });

  it('renders gaps as sum across tickers (73016-style)', async () => {
    /**
     * The "гэпы: N дн" field IS supposed to be the global sum across
     * tickers — that's a meaningful operational metric ("how many
     * gaps exist in total"). Only completeness was buggy.
     *
     * Sample has SBER.gaps=2 + GAZP.gaps=0 + BOND.gaps=50 = 52 total.
     */
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    const gapsText = await screen.findByText(/52\s*дн/);
    expect(gapsText).toBeInTheDocument();
  });

  it('shows the actual cron time (23:00 МСК), not the hardcoded 02:00 МСК', async () => {
    /**
     * Regression: UI label claimed "Ежедневно в 02:00 МСК" but actual
     * crontab is `0 20 * * *` UTC = 23:00 MSK. The hardcoded label was
     * never wired to the real schedule, so operators thought the chain
     * ran in the middle of the night when it actually ran at 23:00 MSK.
     *
     * The cron script comments and crontab entry both confirm 20:00 UTC.
     */
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    // Must NOT show the wrong time
    expect(screen.queryByText(/02:00\s*МСК/)).toBeNull();
    // Must show the actual time
    expect(screen.getByText(/23:00\s*МСК/)).toBeInTheDocument();
  });
});