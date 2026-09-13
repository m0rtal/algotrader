import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@lib/api';
import { ConfirmDialog } from '@features/settings/ConfirmDialog';

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

export function useStartBackfill() {
  const qc = useQueryClient();
  return useMutation<unknown, Error, void>({
    mutationFn: () => api('/admin/backfill/start', { method: 'POST', body: '{}' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backfill-status'] });
      qc.invalidateQueries({ queryKey: ['backfill-pending'] });
    },
  });
}

export function usePendingCount(refetchInterval = 60_000) {
  // The pending endpoint returns the same shape as the inferred return
  // type below; using `any` here would relax the type guard that
  // queryClient gives us, so we declare the expected shape explicitly.
  type Pending = { new: number; stale: number; up_to_date: number; error: number; total: number };
  return useQuery<Pending>({
    queryKey: ['backfill-pending'],
    queryFn: () => api<Pending>('/admin/backfill/pending'),
    refetchInterval,
  });
}

export function useForceReset() {
  const qc = useQueryClient();
  return useMutation<{ deleted_rows: number }, Error, void>({
    mutationFn: () =>
      api<{ deleted_rows: number }>('/admin/backfill/force-reset', {
        method: 'POST',
        body: '{}',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backfill-pending'] });
      qc.invalidateQueries({ queryKey: ['backfill-status'] });
    },
  });
}

// ─── UI ─────────────────────────────────────────────────────────────

export function BackfillTab() {
  const status = useBackfillStatus();
  const pending = usePendingCount();
  const reset = useForceReset();
  const stop = useStopBackfill();
  const start = useStartBackfill();
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
          <span className="text-xs text-[var(--muted-foreground)]">Daily 02:00 MSK scheduler</span>
        </div>

        <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
          <Stat label="Instruments" value={String(pending.data?.total ?? '…')} />
          <Stat label="Up to date" value={String(pending.data?.up_to_date ?? 0)} />
          <Stat label="Bars on disk" value={String(status.data?.total_bars ?? 0)} />
          <Stat label="State" value={status.data?.state ?? '…'} />
        </div>

        <div>
          <h3 className="text-sm font-medium mb-2">Next run will fetch</h3>
          {pending.data && pending.data.new + pending.data.stale + pending.data.error === 0 ? (
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
          <div data-testid="backfill-progress" aria-live="polite">
            <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)] mb-1">
              <span>Active run</span>
              <span>
                {status.data.tickers_done} / {status.data.tickers_total} tickers ({pct}%)
              </span>
            </div>
            <div
              className="h-2 w-full rounded bg-[var(--muted)] overflow-hidden"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={pct}
            >
              <div
                className="h-2 bg-[var(--accent)] transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={running || start.isPending}
            onClick={() => start.mutate()}
            className="rounded border border-[var(--accent)] bg-[var(--accent)]/15 px-3 py-1.5 text-sm font-medium text-[var(--accent)] hover:bg-[var(--accent)]/25 disabled:opacity-50"
          >
            Start backfill
          </button>
          <button
            type="button"
            disabled={!running}
            onClick={() => stop.mutate()}
            className="rounded border border-[var(--border)] px-3 py-1.5 text-sm disabled:opacity-50"
          >
            Stop current run
          </button>
          <button
            type="button"
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
        {/* The single progress bar lives above this block (line ~133),
            gated on status.data.tickers_total > 0. The duplicate that
            used to live here, gated on `running`, was removed on
            2026-09-13 — the user only needs to see one counter on the
            page. Text events live in the global LogStrip at the bottom
            of the viewport; this section shows run state, share done,
            and pending breakdown. */}
    </section>

      {/* Reset metadata confirmation dialog — uses the shared
          ConfirmDialog so Escape, focus trap, and danger styling come
          for free. Body explains the destructive impact; opens only
          once the pending count is loaded so we never show "?". */}
      <ConfirmDialog
        open={showResetConfirm && !!pending.data}
        title="Force full re-backfill?"
        body={
          pending.data
            ? `This wipes every instrument_metadata row, so the next scheduled run (and any subsequent manual trigger) will re-fetch the full history for all ${pending.data.total} instruments. Use this only after a corporate action that restated the series, or if you suspect on-disk bars are corrupt. Routine maintenance is automatic — the daily 02:00 MSK scheduler catches new tickers and stale ones without manual intervention.`
            : 'Loading the instrument count…'
        }
        confirmLabel="Reset metadata"
        cancelLabel="Cancel"
        danger
        onConfirm={() => {
          setShowResetConfirm(false);
          reset.mutate();
        }}
        onCancel={() => setShowResetConfirm(false)}
      />
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
