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

export function useStopBackfill() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, void>({
    mutationFn: () => api('/admin/backfill/stop', { method: 'POST', body: '{}' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backfill-status'] });
    },
  });
}

export function usePendingCount(refetchInterval = 60_000) {
  return useQuery<{ new: number; stale: number; up_to_date: number; error: number; total: number }>({
    queryKey: ['backfill-pending'],
    queryFn: () => api<any>('/admin/backfill/pending'),
    refetchInterval,
  });
}

export function useForceReset() {
  const qc = useQueryClient();
  return useMutation<{ deleted_rows: number }, Error, void>({
    mutationFn: () => api<{ deleted_rows: number }>('/admin/backfill/force-reset', {
      method: 'POST',
      body: '{}',
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backfill-pending'] });
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
  const pending = usePendingCount();
  const reset = useForceReset();
  const stop = useStopBackfill();
  const { events, connected } = useBackfillEvents();
  const [showResetConfirm, setShowResetConfirm] = useState(false);

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
      {/* Top: data + scheduler plan */}
      <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Backfill</h2>
          <span className="text-xs text-[var(--muted-foreground)]">
            Daily 02:00 MSK scheduler
          </span>
        </div>

        <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
          <Stat label="Instruments" value={String(pending.data?.total ?? '…')} />
          <Stat label="Up to date" value={String(pending.data?.up_to_date ?? 0)} />
          <Stat label="Bars on disk" value={String(status.data?.total_bars ?? 0)} />
          <Stat label="State" value={status.data?.state ?? '…'} />
        </div>

        <div>
          <h3 className="text-sm font-medium mb-2">Next run will fetch</h3>
          {pending.data && (pending.data.new + pending.data.stale + pending.data.error) === 0 ? (
            <p className="text-sm text-green-400">
              All {pending.data.total} tickers are up to date. The scheduler has nothing to do.
            </p>
          ) : (
            <>
              <p className="text-sm text-[var(--muted-foreground)] mb-2">
                {/* v8 ignore next */}
                {pending.data
                  ? `${pending.data.new + pending.data.stale + pending.data.error} of ${pending.data.total} tickers need attention.`
                  : 'Counting…'}
              </p>
              <div className="grid grid-cols-3 gap-2 text-xs">
                <Stat label="New (no history)" value={String(pending.data?.new ?? 0)} />
                <Stat label="Stale (>2 days)" value={String(pending.data?.stale ?? 0)} />
                <Stat label="Errored (retry)" value={String(pending.data?.error ?? 0)} />
              </div>
            </>
          )}
        </div>

        {status.data && status.data.tickers_total > 0 && (
          <div>
            <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)] mb-1">
              <span>Active run</span>
              <span>
                {status.data.tickers_done} / {status.data.tickers_total} tickers ({pct}%)
              </span>
            </div>
            <div className="h-2 w-full rounded bg-[var(--muted)] overflow-hidden">
              <div className="h-2 bg-[var(--accent)] transition-all" style={{ width: `${pct}%` }} />
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            disabled={!running}
            onClick={() => stop.mutate()}
            className="rounded border border-[var(--border)] px-3 py-1.5 text-sm disabled:opacity-50"
          >
            Stop current run
          </button>
          <button
            disabled={reset.isPending}
            onClick={() => setShowResetConfirm(true)}
            className="rounded border border-[var(--border)] px-3 py-1.5 text-sm text-red-400 disabled:opacity-50"
          >
            Reset metadata (force full re-fetch)
          </button>
          {reset.isError && (
            <p className="text-sm text-red-400 self-center">{reset.error.message}</p>
          )}
        </div>
      </section>

      {/* Events — the global LogStrip at the bottom of the viewport
          already shows recent system events including this tab's
          backfill run logs (see AppShell for the footer mounting). */}
      <div
        data-testid="backfill-event-log"
        className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4"
      >
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">Recent events</h2>
          <span className={'text-xs ' + (connected ? 'text-green-400' : 'text-zinc-500')}>
            {connected ? '● live (SSE)' : '○ disconnected'}
          </span>
        </div>
        <div
          data-testid="event-log-list"
          className="font-mono text-xs max-h-96 overflow-y-auto bg-[var(--background)] rounded p-3 space-y-1"
        >
          {events.length === 0 && (
            <p className="text-[var(--muted-foreground)]">
              Waiting for events from the next scheduler run… The global log
              footer at the bottom of every page shows system-wide activity.
            </p>
          )}
          {[...events].reverse().map((ev, i) => (
            <div key={`${ev.ts ?? i}-${i}`} className="flex gap-2">
              <span className="text-[var(--muted-foreground)] shrink-0 font-mono w-14">
                {(ev.ts ?? '').slice(11, 19)}
              </span>
              <span
                className={
                  ev.type === 'error' || ev.type === 'done'
                    ? 'text-red-400 shrink-0 w-32'
                    : ev.type === 'ticker_progress'
                      ? 'text-blue-400 shrink-0 w-32'
                      : 'text-zinc-300 shrink-0 w-32'
                }
              >
                {ev.type}
              </span>
              <span className="truncate">
                {JSON.stringify(ev.payload ?? {}).slice(0, 200)}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Reset metadata confirmation dialog */}
      {showResetConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-[var(--card)] border border-[var(--border)] rounded-lg p-6 max-w-md">
            <h3 className="text-lg font-semibold mb-2">Force full re-backfill?</h3>
            <p className="text-sm text-[var(--muted-foreground)] mb-4">
              This wipes every <code className="text-xs">instrument_metadata</code> row, so the next
              scheduled run (and any subsequent manual trigger) will re-fetch the full
              history for all {pending.data?.total ?? '?'} instruments. Use this only after a
              corporate action that restated the series, or if you suspect on-disk bars are
              corrupt. Routine maintenance is automatic — the daily 02:00 MSK scheduler
              catches new tickers and stale ones without manual intervention.
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowResetConfirm(false)}
                className="rounded border border-[var(--border)] px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  reset.mutate();
                  setShowResetConfirm(false);
                }}
                className="rounded bg-red-500 px-3 py-1.5 text-sm text-white"
              >
                Reset metadata
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
