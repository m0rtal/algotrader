import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@lib/api';

// ─── API hooks ──────────────────────────────────────────────────────

type BackfillStatus = {
  state: string;
  run_id: number | null;
  tickers_done: number;
  tickers_total: number;
  total_bars: number;
  last_run: { id: number; ts: string; level: string; message: string } | null;
};

export function useBackfillStatus() {
  return useQuery<BackfillStatus>({
    queryKey: ['backfill-status'],
    queryFn: () => api<BackfillStatus>('/admin/backfill/status'),
    refetchInterval: 5000,
  });
}

export function useStartBackfill() {
  const qc = useQueryClient();
  return useMutation<{ run_id: number }, Error, { history_years?: number } | void>({
    mutationFn: (body) =>
      api<{ run_id: number }>('/admin/backfill/start', {
        method: 'POST',
        body: JSON.stringify(body ?? {}),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backfill-status'] });
    },
  });
}

export function useStopBackfill() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, void>({
    mutationFn: () => api('/admin/backfill/stop', { method: 'POST', body: '{}' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backfill-status'] });
    },
  });
}

type BackfillEvent = {
  type: string;
  run_id: number;
  ts: string;
  payload: Record<string, unknown>;
};

// EventSource-based live log stream. Auto-reconnects on close with
// exponential backoff (capped at 10s). Keeps the last 100 events in
// state so a brief disconnect doesn't lose history.
export function useBackfillEvents() {
  const [events, setEvents] = useState<BackfillEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const retryDelay = useRef(500);

  useEffect(() => {
    let cancelled = false;
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      // Build a relative URL; EventSource uses the current page origin.
      es = new EventSource('/api/admin/backfill/events');

      es.onopen = () => {
        retryDelay.current = 500;
        setConnected(true);
      };

      const onEvent = (raw: MessageEvent) => {
        try {
          const ev = JSON.parse(raw.data) as BackfillEvent;
          setEvents((prev) => {
            const next = [...prev, ev];
            if (next.length > 100) next.shift();
            return next;
          });
        } catch {
          // ignore malformed events
        }
      };

      // Generic listener — the server uses the SSE `event:` field for
      // type but `EventSource` only fires typed listeners if we add
      // them. We use addEventListener so we get all event types in
      // one handler.
      for (const evtType of ['status', 'log', 'ticker_progress', 'done']) {
        es.addEventListener(evtType, onEvent as EventListener);
      }

      es.onerror = () => {
        setConnected(false);
        es?.close();
        // Reconnect with exponential backoff.
        const delay = Math.min(retryDelay.current, 10_000);
        retryDelay.current = Math.min(retryDelay.current * 2, 10_000);
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
    };
  }, []);

  return { events, connected };
}

// ─── UI ─────────────────────────────────────────────────────────────

export function BackfillTab() {
  const status = useBackfillStatus();
  const start = useStartBackfill();
  const stop = useStopBackfill();
  const { events, connected } = useBackfillEvents();
  const [showStartDialog, setShowStartDialog] = useState(false);
  const [historyYears, setHistoryYears] = useState(5);

  const running =
    status.data?.state === 'running' ||
    status.data?.state === 'backfilling' ||
    status.data?.state === 'discovering' ||
    status.data?.state === 'stopping';

  const pct =
    status.data && status.data.tickers_total > 0
      ? Math.round((status.data.tickers_done / status.data.tickers_total) * 100)
      : 0;

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Status */}
      <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4">
        <h2 className="text-lg font-semibold mb-3">Backfill</h2>
        <div className="grid grid-cols-4 gap-4 text-sm">
          <Stat label="State" value={status.data?.state ?? '…'} />
          <Stat
            label="Tickers"
            value={status.data ? `${status.data.tickers_done} / ${status.data.tickers_total}` : '…'}
          />
          <Stat label="Bars written" value={String(status.data?.total_bars ?? 0)} />
          <Stat label="Last run" value={status.data?.last_run?.message ?? '—'} />
        </div>
        {status.data && status.data.tickers_total > 0 && (
          <div className="mt-3">
            <div className="h-2 w-full rounded bg-[var(--muted)] overflow-hidden">
              <div className="h-2 bg-[var(--accent)] transition-all" style={{ width: `${pct}%` }} />
            </div>
            <p className="text-xs text-[var(--muted-foreground)] mt-1">{pct}% complete</p>
          </div>
        )}
        <div className="mt-4 flex gap-2">
          <button
            disabled={running || start.isPending}
            onClick={() => setShowStartDialog(true)}
            className="rounded bg-[var(--accent)] px-3 py-1.5 text-sm disabled:opacity-50"
          >
            Start backfill
          </button>
          <button
            disabled={!running || stop.isPending}
            onClick={() => stop.mutate()}
            className="rounded border border-[var(--border)] px-3 py-1.5 text-sm disabled:opacity-50"
          >
            Stop
          </button>
          {start.isError && (
            <p className="text-sm text-red-400 self-center">{(start.error as Error).message}</p>
          )}
        </div>
      </section>

      {/* Logs */}
      <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Logs</h2>
          <span className={'text-xs ' + (connected ? 'text-green-400' : 'text-zinc-500')}>
            {connected ? '● connected' : '○ disconnected'}
          </span>
        </div>
        <div className="font-mono text-xs max-h-96 overflow-y-auto bg-[var(--background)] rounded p-3 space-y-1">
          {events.length === 0 && (
            <p className="text-[var(--muted-foreground)]">Waiting for events…</p>
          )}
          {[...events].reverse().map((ev, i) => (
            <div key={`${ev.ts}-${i}`} className="flex gap-2">
              <span className="text-[var(--muted-foreground)] shrink-0">{ev.ts.slice(11, 19)}</span>
              <span
                className={
                  ev.type === 'error' || ev.type === 'done'
                    ? 'text-red-400'
                    : ev.type === 'ticker_progress'
                      ? 'text-blue-400'
                      : 'text-zinc-300'
                }
              >
                {ev.type}
              </span>
              <span className="truncate">{JSON.stringify(ev.payload).slice(0, 200)}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Confirm dialog */}
      {showStartDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-6 max-w-md">
            <h3 className="text-lg font-semibold mb-2">Start backfill?</h3>
            <p className="text-sm text-[var(--muted-foreground)] mb-4">
              Will fetch up to N years of daily bars for every MOEX instrument from the broker
              sandbox. Rate-limited at 14 req/min; 250 tickers take ~17 minutes.
            </p>
            <label className="block text-sm mb-2">
              History (years):
              <input
                type="number"
                min={1}
                max={10}
                value={historyYears}
                onChange={(e) => setHistoryYears(Number(e.target.value))}
                className="ml-2 w-16 rounded border border-[var(--border)] bg-[var(--background)] px-2 py-1"
              />
            </label>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowStartDialog(false)}
                className="rounded border border-[var(--border)] px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  start.mutate({ history_years: historyYears });
                  setShowStartDialog(false);
                }}
                className="rounded bg-[var(--accent)] px-3 py-1.5 text-sm"
              >
                Start
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-[var(--muted-foreground)] uppercase tracking-wide">{label}</p>
      <p className="font-mono text-base mt-1">{value}</p>
    </div>
  );
}
