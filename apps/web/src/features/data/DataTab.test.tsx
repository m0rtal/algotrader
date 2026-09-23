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

  it('renders Полнота and Гэпы as two separate KPI blocks (no inline subtitle)', async () => {
    /**
     * Redesign: completeness (98.3%) and gaps count (73016 дн) are
     * different operational metrics and should be visually distinct.
     * Previously the gaps count was crammed into a tiny grey subtitle
     * next to the Полнота percentage — easy to miss in the UI.
     *
     * Layout contract:
     *   - One KPI block labeled "Полнота" containing only the percentage.
     *   - One KPI block labeled "Гэпы" containing only the number + "дн".
     *   - The "гэпы: N дн" inline subtitle inside the Полнота cell
     *     is removed (was the cramped form).
     *
     * Note: "Гэпы" also appears as a TABLE COLUMN HEADER in the per-
     * ticker table — that's expected and not the same element. We
     * assert the metric block exists by looking for the value next to
     * its label in the KPI section.
     */
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    // Find Полнота block by its label
    const polnotaLabel = screen.getByText(/^Полнота$/);
    expect(polnotaLabel).toBeInTheDocument();

    // Find Гэпы block by its label. The KPI block has "Гэпы" as
    // label, but the per-ticker table also has a "Гэпы" column
    // header — both are expected to exist. We assert that at least
    // one element with that exact text is in the document.
    expect(screen.getAllByText(/^Гэпы$/).length).toBeGreaterThan(0);

    // Belt-and-suspenders: the OLD inline "гэпы: N дн" form inside
    // the Полнота cell must be gone.
    const allText = document.body.textContent ?? '';
    expect(allText).not.toMatch(/гэпы:\s*\d/);
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

  it('renders the KPI grid compactly: period shows "since YYYY" since Tinkoff limits history to 5y', async () => {
    /**
     * Tinkoff investAPI sandbox returns ~5 years of historical bars
     * regardless of instrument listing date. Even SBER (listed 1996)
     * only has bars from 2021-08-10 onwards in our DB. Verified by
     * inspecting the bars table directly.
     *
     * Layout contract: the KPI Период block must show "since 2021"
     * (5 chars) — both compact enough to fit the sm:grid-cols-5 cell
     * AND unambiguous that this is a Tinkoff API limit, not our data
     * being shallow.
     */
    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );
    // The compact period must be present (year-only "с 2021" prefix).
    // Sample has firstDate=2021-09-01, so the compact form is "с 2021".
    const matches = await screen.findAllByText(
      (content) => /^с\s+2021/.test(content.trim()),
      undefined,
      { timeout: 3000 },
    );
    expect(matches.length).toBeGreaterThan(0);

    // Belt-and-suspenders: the year-month or day-precision format must
    // NOT be in the KPI period block. There are two "Период" labels in
    // the DOM (KPI block + table column header) — pick the first one.
    const periodLabels = screen.getAllByText(/^Период$/);
    const periodValue = periodLabels[0].parentElement?.querySelector('p:nth-of-type(2)');
    expect(periodValue?.textContent?.trim()).toMatch(/^с\s+2021/);
  });

  it('renders all 5 KPI blocks with … placeholder while data is loading', async () => {
    /**
     * Regression: when backend is slow/down the KPI grid showed mixed
     * placeholders — some `…`, some `0` (status.total_bars default),
     * some `—` (totalGaps>0 false branch). Operators couldn't tell
     * whether 0 was "really zero" or "haven't loaded yet".
     *
     * Contract: when data is loading, EVERY block renders `…`. Once
     * data is loaded, real numbers replace the placeholder.
     *
     * Test simulates loading by stalling the backfill-status and
     * tickers endpoints indefinitely. Use a fresh QueryClient so
     * the shared module-level cache doesn't already have data from
     * earlier tests.
     */
    server.use(
      http.get('/api/admin/backfill/status', async () => {
        await new Promise((r) => setTimeout(r, 10_000));
        return HttpResponse.json(defaultStatus);
      }),
      http.get('/api/admin/backfill/pending', async () => {
        await new Promise((r) => setTimeout(r, 10_000));
        return HttpResponse.json(defaultPending);
      }),
      http.get('/api/tickets', async () => {
        await new Promise((r) => setTimeout(r, 10_000));
        return HttpResponse.json(sampleTickers);
      }),
    );
    const freshClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
    });
    const FreshWrap = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={freshClient}>{children}</QueryClientProvider>
    );
    FreshWrap.displayName = 'FreshQueryClientWrapper';
    render(
      <FreshWrap>
        <DataTab />
      </FreshWrap>,
    );
    await screen.findByRole('heading', { name: 'Данные' });

    const allText = document.body.textContent ?? '';
    const ellipsisMatches = allText.match(/…/g) ?? [];
    expect(ellipsisMatches.length).toBeGreaterThanOrEqual(5);
    const barsBlock = screen.getByText(/^Баров на диске$/).parentElement;
    expect(barsBlock?.textContent).not.toMatch(/^\s*0\s*$/);
    const gapsLabels = screen.getAllByText(/^Гэпы$/);
    const gapsBlock = gapsLabels[0].parentElement;
    expect(gapsBlock?.textContent).not.toMatch(/^\s*—\s*$/);
  });

  it('renders Период using the earliest NON-EMPTY firstDate (zero-bar figis have empty firstDate and must be ignored)', async () => {
    /**
     * Regression test for PR #TBD: the prod DB has 37 tradable figis with
     * zero bars (zero-bar coverage gap from sanctions / delisting). The
     * backend serialises those as `firstDate: ""` / `lastDate: ""` (see
     * data_reads.py line ~265). JS string comparison says `"" < "2013-..."`
     * is true, so a naive
     *   tickers.reduce((acc, t) => (acc === null || t.firstDate < acc ? t.firstDate : acc), null)
     * would pick the empty string and the UI would render the literal
     * "…" placeholder forever (the period block is gated on
     * `firstDate ? "с ${firstDate.slice(0,4)}" : "…"`).
     *
     * Correct behaviour: skip empty strings in the reduce; render the
     * earliest real firstDate (which on prod is 2013-03-25, displayed
     * as "с 2013").
     */
    server.use(
      http.get('/api/tickers', () =>
        HttpResponse.json([
          // 37 stub zero-bar figis to mirror prod universe.
          ...Array.from({ length: 37 }, (_, i) => ({
            symbol: `STUB${i}`,
            name: '',
            sector: '',
            price: 0,
            bars: 0,
            firstDate: '',
            lastDate: '',
            fileSize: 0,
            gaps: 0,
          })),
          {
            symbol: 'OLDEST',
            name: 'Eldest',
            sector: '',
            price: 0,
            bars: 3000,
            firstDate: '2013-03-25',
            lastDate: '2026-09-22',
            fileSize: 0,
            gaps: 0,
          },
          {
            symbol: 'MID',
            name: 'Mid',
            sector: '',
            price: 0,
            bars: 1500,
            firstDate: '2021-09-01',
            lastDate: '2026-09-22',
            fileSize: 0,
            gaps: 0,
          },
        ]),
      ),
    );

    render(
      <Wrap>
        <DataTab />
      </Wrap>,
    );

    const periodLabels = await screen.findAllByText(/^Период$/);
    // Wait for the period VALUE (not the static "Период" label) to render.
    // The KPI block shows "…" until useTickers resolves; only then does it
    // become "с 2013". Use the same await-findAllByText-with-callback
    // pattern as the sibling test on line ~351 so we synchronise on the
    // post-resolve DOM, not the placeholder.
    await screen.findAllByText(
      (content) => /^с\s+2013/.test(content.trim()),
      undefined,
      { timeout: 3000 },
    );
    const periodValue = periodLabels[0].parentElement?.querySelector('p:nth-of-type(2)');
    expect(periodValue?.textContent?.trim()).toMatch(/^с\s+2013/);
  });
});
