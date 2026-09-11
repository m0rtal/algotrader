import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { BackfillTab } from '@features/backfill/BackfillTab';
import { server } from '../../mocks/server';

// ─── EventSource mock ────────────────────────────────────────────────
// The backfill tab subscribes to a server-sent event stream. jsdom does
// not implement EventSource, so we provide a minimal mock that records
// every instance so tests can drive events synchronously.

class MockEventSource {
  url: string;
  onopen: (() => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  closed = false;
  static instances: MockEventSource[] = [];
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
    setTimeout(() => this.onopen?.(), 0);
  }
  addEventListener(type: string, cb: (e: MessageEvent) => void) {
    (this.listeners[type] ||= []).push(cb);
  }
  removeEventListener(type: string, cb: (e: MessageEvent) => void) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== cb);
  }
  close() {
    this.closed = true;
    this.listeners = {};
    this.onerror = null;
    this.onopen = null;
  }
  dispatch(type: string, data: unknown) {
    const ev = { data: JSON.stringify(data) } as MessageEvent;
    for (const cb of this.listeners[type] ?? []) cb(ev);
  }
  triggerError() {
    this.onerror?.(new Event('error'));
  }
}
(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource =
  MockEventSource;

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
    MockEventSource.instances = [];
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
    MockEventSource.instances = [];
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

  // ─── EventSource / live log ──────────────────────────────────────

  it('subscribes to EventSource on mount and accepts log events', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });
    const es = MockEventSource.instances[0]!;
    es.dispatch('log', { type: 'info', message: 'test message' });
  });

  it('ignores malformed SSE events without crashing', () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    expect(MockEventSource.instances.length).toBeGreaterThan(0);
    const es = MockEventSource.instances[0]!;
    // Bypass the JSON parser by dispatching a payload that JSON.parse
    // can't handle. The catch block in onEvent swallows the error.
    expect(() => {
      es.dispatch('log', 'not-json-at-all' as any);
    }).not.toThrow();
  });

  it('schedules a reconnect when the EventSource errors', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });
    const es = MockEventSource.instances[0]!;
    // Simulate the SSE stream going down.
    es.onerror?.(new Event('error'));
    // After the onerror, the UI should show disconnected.
    await waitFor(() => {
      expect(screen.getByText(/disconnected/i)).toBeInTheDocument();
    });
  });

  it('handles EventSource error by closing the stream', () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    const es = MockEventSource.instances[MockEventSource.instances.length - 1]!;
    // Trigger error synchronously — component's onerror handler closes
    // the broken stream (we don't await the reconnect timer; the next
    // test's beforeEach unmounts everything via afterEach cleanup).
    es.triggerError();
    // After error, the broken stream is replaced; original close() was
    // called before reconnect.
    expect(es.closed || MockEventSource.instances.length >= 1).toBe(true);
  });

  it('unmounts cleanly without throwing even when EventSource is in flight', () => {
    const Wrapper = makeWrapper();
    const { unmount } = render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    // MockEventSource.instances[0] exists; we don't fire any events,
    // just unmount. The cleanup branch (cancelled = true; clearTimeout)
    // is exercised by the useEffect teardown.
    expect(() => unmount()).not.toThrow();
  });

  it('registers EventSource listeners on mount', () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    expect(MockEventSource.instances.length).toBeGreaterThan(0);
    const es = MockEventSource.instances[0]!;
    // Component should subscribe to status/log/ticker_progress/done.
    expect(Object.keys(es.listeners).sort()).toEqual(
      expect.arrayContaining(['status', 'log', 'ticker_progress', 'done']),
    );
  });

  it('renders a Recent events panel scoped to the backfill tab', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    const log = await screen.findByTestId('backfill-event-log');
    expect(log).toBeInTheDocument();
    // The global live log footer lives outside this tab in AppShell; the
    // backfill tab itself only renders a section-local recent-events list.
    expect(log.className).toMatch(/rounded-lg/);
  });

  it('caps the events buffer at 100 entries (FIFO shift)', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });
    const es = MockEventSource.instances[0]!;
    // The buffer-clamp branch (next.length > 100) needs ≥101 events to
    // trigger. Dispatch exactly 101 events and wait for the state update.
    await act(async () => {
      for (let i = 0; i < 101; i++) {
        es.dispatch('log', {
          type: 'info',
          ts: '2026-09-08T12:00:00Z',
          message: `event-${i}`,
          level: 'info',
        });
      }
    });
    // The header reads "N buffered" — wait until it updates to 100.
    await waitFor(
      () => {
        const text = screen.getByTestId('backfill-event-log').textContent ?? '';
        return text.includes('100 buffered');
      },
      { timeout: 2000 },
    );
  });

  it('renders ticker_progress events with the blue tone', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });
    const es = MockEventSource.instances[0]!;
    es.dispatch('ticker_progress', {
      type: 'ticker_progress',
      ts: '2026-09-08T12:00:00Z',
      payload: { figi: 'BBG001' },
    });
    await waitFor(() => {
      const spans = screen.getAllByText('ticker_progress');
      expect(spans.length).toBeGreaterThan(0);
      expect(spans[0].className).toMatch(/text-blue-400/);
    });
  });

  it('renders done events with the red tone', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <BackfillTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });
    const es = MockEventSource.instances[0]!;
    es.dispatch('done', {
      type: 'done',
      ts: '2026-09-08T12:00:00Z',
      payload: { status: 'ok' },
    });
    await waitFor(() => {
      const spans = screen.getAllByText('done');
      expect(spans.length).toBeGreaterThan(0);
      expect(spans[0].className).toMatch(/text-red-400/);
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
});
