import { describe, expect, it, beforeEach, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

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
  }
  dispatch(type: string, data: unknown) {
    const ev = { data: JSON.stringify(data) } as MessageEvent;
    for (const cb of this.listeners[type] ?? []) cb(ev);
  }
}
(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource =
  MockEventSource;

import { BackfillTab } from './BackfillTab';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0, gcTime: 0 },
    },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('BackfillTab', () => {
  beforeEach(() => {
    localStorage.clear();
    MockEventSource.instances = [];
    vi.restoreAllMocks();
  });

  it('renders idle state with controls', async () => {
    const Wrapper = makeWrapper();
    render(<BackfillTab />, { wrapper: Wrapper });
    await waitFor(() => {
      expect(screen.getByText(/Start backfill/i)).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText(/idle/i)).toBeInTheDocument();
    });
  });

  it('renders Stop button as disabled when idle', () => {
    const Wrapper = makeWrapper();
    render(<BackfillTab />, { wrapper: Wrapper });
    const stopBtn = screen.queryByRole('button', { name: /^Stop$/i });
    expect(stopBtn).toBeInTheDocument();
    expect(stopBtn).toBeDisabled();
  });

  it('opens start dialog when clicking start, then sends mutation on confirm', async () => {
    const Wrapper = makeWrapper();
    render(<BackfillTab />, { wrapper: Wrapper });
    await waitFor(() => screen.getByText(/Start backfill/i));
    fireEvent.click(screen.getByRole('button', { name: /Start backfill/i }));
    await waitFor(() => screen.getByText(/Start backfill\?/i));
    // Dialog has a separate "Start" button to confirm.
    const dialogBtn = screen.getAllByRole('button', { name: /^Start$/i });
    fireEvent.click(dialogBtn[dialogBtn.length - 1]!);
    await waitFor(() => {
      const stored = localStorage.getItem('algotrader.mock_backfill_state');
      expect(stored).not.toBeNull();
    });
  });

  it('closes start dialog on cancel', async () => {
    const Wrapper = makeWrapper();
    render(<BackfillTab />, { wrapper: Wrapper });
    await waitFor(() => screen.getByText(/Start backfill/i));
    fireEvent.click(screen.getByText(/Start backfill/i));
    await waitFor(() => screen.getByText(/Start backfill\?/i));
    fireEvent.click(screen.getByText(/Cancel/i));
    await waitFor(() => {
      expect(screen.queryByText(/Start backfill\?/i)).not.toBeInTheDocument();
    });
  });

  it('subscribes to EventSource on mount and accepts log events', async () => {
    const Wrapper = makeWrapper();
    render(<BackfillTab />, { wrapper: Wrapper });
    await waitFor(() => {
      expect(MockEventSource.instances.length).toBeGreaterThan(0);
    });
    const es = MockEventSource.instances[0]!;
    es.dispatch('log', { type: 'info', message: 'test message' });
  });

  it('shows stop button and error state when backfilling', async () => {
    localStorage.setItem(
      'algotrader.mock_backfill_state',
      JSON.stringify({
        state: 'backfilling',
        run_id: 1,
        tickers_total: 100,
        tickers_done: 50,
        total_bars: 1234,
        last_run: { id: 1, ts: '2026-01-01', level: 'info', message: 'progress' },
      }),
    );
    const Wrapper = makeWrapper();
    render(<BackfillTab />, { wrapper: Wrapper });
    await waitFor(() => {
      expect(screen.getByText(/Stop\b/)).toBeInTheDocument();
    });
  });

  it('shows stopped state', async () => {
    localStorage.setItem(
      'algotrader.mock_backfill_state',
      JSON.stringify({
        state: 'stopping',
        run_id: 1,
        tickers_total: 100,
        tickers_done: 25,
        total_bars: 500,
        last_run: null,
      }),
    );
    const Wrapper = makeWrapper();
    render(<BackfillTab />, { wrapper: Wrapper });
    await waitFor(() => {
      expect(screen.getByText(/stopping/i)).toBeInTheDocument();
    });
  });

  it('toggles history years input', async () => {
    const Wrapper = makeWrapper();
    render(<BackfillTab />, { wrapper: Wrapper });
    await waitFor(() => screen.getByText(/Start backfill/i));
    fireEvent.click(screen.getByRole('button', { name: /Start backfill/i }));
    const input = (await screen.findByLabelText(/History \(years\)/i)) as HTMLInputElement;
    fireEvent.change(input, { target: { value: '5' } });
    expect(input.value).toBe('5');
  });

  it('renders discovered and done states without error', async () => {
    for (const state of ['discovering', 'done', 'error']) {
      localStorage.setItem(
        'algotrader.mock_backfill_state',
        JSON.stringify({
          state,
          run_id: 1,
          tickers_total: 10,
          tickers_done: 5,
          total_bars: 100,
          last_run: null,
        }),
      );
      const Wrapper = makeWrapper();
      const { unmount } = render(<BackfillTab />, { wrapper: Wrapper });
      await waitFor(() => {
        expect(screen.getByText(new RegExp(state, 'i'))).toBeInTheDocument();
      });
      unmount();
      localStorage.clear();
    }
  });
});
