import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { BackfillTab } from '@features/backfill/BackfillTab';
import { server } from '../../mocks/server';

// The previous SSE-based `useBackfillEvents` hook was removed: the
// dedicated event log duplicated the global LogStrip footer and
// offered no extra signal once the page already shows scheduler state,
// tickers done/total, and a single live progress bar. All EventSource
// mocking scaffolding is gone with it.

// ─── Default fixtures ────────────────────────────────────────────────
// MSW handlers are pure passthroughs in this codebase — every test must
// install its own canned data via server.use(). The fixtures below
// match the shape returned by the real backend on a healthy system.

const defaultStatus = {
  state: 'idle',
  run_id: null,
  tickers_total: 0,
  tickers_done: 0,
  total_bars: 0,
  last_run: null,
};

const defaultPending = {
  new: 0,
  stale: 0,
  up_to_date: 0,
  error: 0,
  total: 0,
};

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: 0 },
    },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('BackfillTab', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // Default fixtures: idle system, nothing pending. Individual tests
    // stack additional handlers via server.use() to override these.
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json(defaultStatus),
      ),
      http.get('/api/admin/backfill/pending', () =>
        HttpResponse.json(defaultPending),
      ),
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

  // ─── Idle / default state ────────────────────────────────────────

  it('renders idle state with scheduler-mode hint', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/02:00 MSK scheduler/i)).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText(/idle/i)).toBeInTheDocument();
    });
  });

  it('renders Stop current run button as disabled when idle', () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    const stopBtn = screen.queryByRole('button', { name: /Stop current run/i });
    expect(stopBtn).toBeInTheDocument();
    expect(stopBtn).toBeDisabled();
  });

  it('renders Start backfill button enabled when idle', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    const startBtn = await screen.findByRole('button', { name: /Start backfill/i });
    expect(startBtn).toBeInTheDocument();
    expect(startBtn).not.toBeDisabled();
  });

  it('clicking Start backfill POSTs to /admin/backfill/start and refreshes status', async () => {
    let startCalled = 0;
    server.use(
      http.post('/api/admin/backfill/start', () => {
        startCalled += 1;
        return HttpResponse.json({ ok: true, run_id: 42 });
      }),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    const startBtn = await screen.findByRole('button', { name: /Start backfill/i });
    fireEvent.click(startBtn);
    await waitFor(() => {
      expect(startCalled).toBe(1);
    });
  });

  it('shows scheduler-mode hint text on the backfill tab', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => screen.getByText(/02:00 MSK scheduler/i));
  });

  it('renders the scheduler plan in real numbers', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/Instruments/i)).toBeInTheDocument();
      expect(screen.getByText(/Up to date/i)).toBeInTheDocument();
      expect(screen.getByText(/Bars on disk/i)).toBeInTheDocument();
      expect(screen.getByText(/New \(no history\)/i)).toBeInTheDocument();
      expect(screen.getByText(/Stale \(>2 days\)/i)).toBeInTheDocument();
      expect(screen.getByText(/Errored \(retry\)/i)).toBeInTheDocument();
    });
  });

  // ─── Reset dialog ────────────────────────────────────────────────

  it('triggers reset mutation on confirm', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => screen.getByText(/Reset metadata/i));
    fireEvent.click(screen.getByText(/Reset metadata/i));
    await waitFor(() => screen.getByText(/Force full re-backfill/i));
    const confirmBtns = screen.getAllByRole('button', { name: /Reset metadata/i });
    // The second one is inside the dialog.
    fireEvent.click(confirmBtns[confirmBtns.length - 1]!);
    // Dialog closes, no error toast.
    await waitFor(() => {
      expect(screen.queryByText(/Force full re-backfill/i)).not.toBeInTheDocument();
    });
  });

  it('opens reset-metadata dialog and cancels', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => screen.getByText(/Reset metadata/i));
    fireEvent.click(screen.getByText(/Reset metadata/i));
    await waitFor(() => screen.getByText(/Force full re-backfill/i));
    fireEvent.click(screen.getByText(/^Cancel$/i));
    await waitFor(() => {
      expect(screen.queryByText(/Force full re-backfill/i)).not.toBeInTheDocument();
    });
  });

  it('shows reset error message when force_reset fails', async () => {
    server.use(
      http.post('/api/admin/backfill/force-reset', () =>
        new HttpResponse('boom', { status: 500 }),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => screen.getByText(/Reset metadata/i));
    fireEvent.click(screen.getByText(/Reset metadata/i));
    await waitFor(() => screen.getByText(/Force full re-backfill/i));
    fireEvent.click(screen.getByText(/^Reset metadata$/i));
    // The error message bubbles up next to the buttons.
    await waitFor(() => {
      expect(screen.getByText(/500/i)).toBeInTheDocument();
    });
  });

  // ─── Running states — server.use() overrides ─────────────────────

  it('shows Stop button enabled when backfilling', async () => {
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json({
          state: 'backfilling',
          run_id: 1,
          tickers_total: 100,
          tickers_done: 50,
          total_bars: 1234,
          last_run: { id: 1, ts: '2026-01-01', level: 'info', message: 'progress' },
        }),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/Stop\b/)).toBeInTheDocument();
    });
  });

  it('shows Stop button when status.state is discovering', async () => {
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json({
          state: 'discovering',
          run_id: 1,
          tickers_total: 10,
          tickers_done: 0,
          total_bars: 0,
          last_run: null,
        }),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => {
      const stopBtn = screen.queryByText(/Stop current run/i);
      expect(stopBtn).toBeTruthy();
      expect((stopBtn as HTMLButtonElement).disabled).toBe(false);
    });
  });

  it('shows stopping state', async () => {
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json({
          state: 'stopping',
          run_id: 1,
          tickers_total: 100,
          tickers_done: 25,
          total_bars: 500,
          last_run: null,
        }),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/stopping/i)).toBeInTheDocument();
    });
  });

  it('renders discovered, done and error states without error', async () => {
    for (const state of ['discovering', 'done', 'error']) {
      server.use(
        http.get('/api/admin/backfill/status', () =>
          HttpResponse.json({
            state,
            run_id: 1,
            tickers_total: 10,
            tickers_done: 5,
            total_bars: 100,
            last_run: null,
          }),
        ),
      );
      const Wrapper = makeWrapper();
      const { unmount } = render(
        <Wrapper>
          <BackfillTab />
        </Wrapper>,
      );
      await waitFor(() => {
        expect(screen.getByText(new RegExp(state, 'i'))).toBeInTheDocument();
      });
      unmount();
    }
  });

  // ─── Pending loading state ───────────────────────────────────────

  it('shows Counting… while pending data is still loading', () => {
    // Block the pending endpoint so the query stays in loading state.
    server.use(
      http.get('/api/admin/backfill/pending', () => new Promise(() => {})),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    // The Stat labels render unconditionally; the data-side text shows
    // 'Counting…' because pending.data is undefined while the query is
    // pending.
    expect(screen.getByText(/Instruments/i)).toBeInTheDocument();
    expect(screen.getByText(/Counting…/i)).toBeInTheDocument();
  });

  // ─── Stop mutation ───────────────────────────────────────────────

  it('exposes stop mutation when in backfilling state', async () => {
    // The status handler flips from backfilling → idle once the stop
    // POST fires. This mirrors how the real backend reports the state
    // change: the client POSTs /stop, then invalidates ['backfill-status']
    // which refetches the status, which now reflects the idle state.
    let stopped = false;
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json(
          stopped
            ? {
                state: 'idle',
                run_id: null,
                tickers_total: 0,
                tickers_done: 0,
                total_bars: 0,
                last_run: null,
              }
            : {
                state: 'backfilling',
                run_id: 7,
                tickers_total: 50,
                tickers_done: 10,
                total_bars: 200,
                last_run: null,
              },
        ),
      ),
      http.post('/api/admin/backfill/stop', () => {
        stopped = true;
        return HttpResponse.json({ ok: true });
      }),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(
      () => {
        const btn = screen.queryByText(/Stop current run/i) as HTMLButtonElement | null;
        expect(btn).toBeTruthy();
        expect(btn!.disabled).toBe(false);
      },
      { timeout: 3000 },
    );
    fireEvent.click(screen.getByText(/Stop current run/i));
    // After stop, the button is disabled again because state returned
    // to idle via the refetched status.
    await waitFor(
      () => {
        const btn = screen.queryByText(/Stop current run/i) as HTMLButtonElement | null;
        expect(btn).toBeTruthy();
        expect(btn!.disabled).toBe(true);
      },
      { timeout: 3000 },
    );
  });

  // ─── Progress bar ────────────────────────────────────────────────

  it('does not render the progress bar when idle', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/Daily 02:00 MSK scheduler/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId('backfill-progress')).toBeNull();
  });

  it('renders the progress bar with the right percentage while running', async () => {
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json({
          state: 'backfilling',
          run_id: 1,
          tickers_done: 30,
          tickers_total: 100,
          total_bars: 50000,
          last_run: null,
        }),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    const bar = await screen.findByTestId('backfill-progress');
    expect(bar.textContent).toMatch(/30\s*\/\s*100 tickers/);
    expect(bar.textContent).toMatch(/\(30%\)/);
    // The role="progressbar" element exposes aria-valuenow for screen readers.
    const progressEl = screen.getByRole('progressbar');
    expect(progressEl.getAttribute('aria-valuenow')).toBe('30');
  });
  it('does not render two duplicate progress bars while running', async () => {
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json({
          state: 'backfilling',
          run_id: 1,
          tickers_done: 2366,
          tickers_total: 3806,
          total_bars: 50000,
          last_run: null,
        }),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    // Both bars used to render here — fixed 2026-09-13 to drop the
    // duplicate that lived inside the Recent events panel. The page
    // should now show exactly one 'Active run' progress block.
    await waitFor(() => {
      expect(screen.getByText(/2366 \/ 3806/)).toBeInTheDocument();
    });
    expect(screen.getAllByText(/Active run/).length).toBe(1);
  });
});
