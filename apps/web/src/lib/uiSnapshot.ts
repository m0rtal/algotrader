"""Shared hook for the unified UI snapshot.

The snapshot wraps the three reads the Data tab used to issue
on mount (``tickers`` + ``pending`` + ``health`` summary) into one
HTTP round-trip. The backend writes a JSON file off the SQLite
result of those queries so the round-trip is a single FileResponse
sendfile — about 1 ms on prod. See
``apps/api/src/algotrader_api/ui_snapshot.py`` for the producer.

The prefetch hint in ``index.html`` makes the browser start
downloading the JSON while the React bundle is still parsing, so
by the time ``useUiSnapshot``'s queryFn runs the response is
already in the HTTP cache — the load budget is roughly the
HTML parse + JS parse + tick.
"""
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { api } from '@lib/api';
import { TickerSchema, type Ticker } from '@algotrader/shared';

// `Pending` deliberately duplicates the type in
// `features/backfill/hooks.ts` because the snapshot shape carries
// extra fields (`by_health`, `worst`) that the live endpoint
// doesn't always emit. Keeping the snapshot schema separate from
// the live endpoint schema avoids a "type drift" bug where the
// frontend optimistically reads fields that the live endpoint
// wouldn't populate.
const SnapshotPendingSchema = z.object({
  new: z.number(),
  stale: z.number(),
  up_to_date: z.number(),
  error: z.number(),
  total: z.number(),
  by_health: z.object({
    '100': z.number(),
    '99-90': z.number(),
    '89-50': z.number(),
    '<50': z.number(),
  }),
  worst: z.array(
    z.object({
      figi: z.string(),
      ticker: z.string(),
      health_score: z.number(),
    }),
  ),
});

export const UiSnapshotSchema = z.object({
  generated_at: z.number(),
  version: z.number(),
  tickers: z.array(TickerSchema),
  pending_counts: SnapshotPendingSchema,
  health: z.record(z.string(), z.object({ health_score: z.number() })),
});

export type UiSnapshot = z.infer<typeof UiSnapshotSchema>;
export type UiSnapshotPending = z.infer<typeof SnapshotPendingSchema>;

export function useUiSnapshot() {
  return useQuery<UiSnapshot>({
    queryKey: ['ui-snapshot'],
    queryFn: async () => {
      const data = await api<unknown>('/ui-snapshot');
      return UiSnapshotSchema.parse(data);
    },
    // The snapshot has its own freshness control on the server
    // (5s budget + version stamp). We refetch on focus to pick up
    // operator-driven changes (force-reset) without depending on
    // the worker pump to refresh.
    refetchOnWindowFocus: true,
    // No refetchInterval — the worker drives freshness via the
    // snapshot rewrite. The Data tab already polls
    // backfill-status every 5s, which is the right cadence for
    // seeing recent bars; the snapshot follows on the next
    // /api/ui-snapshot hit, which happens on tab focus.
  });
}

/**
 * Convenience selector: extract only the Pending shape that the
 * DataTab renders. Same data as `usePendingCount()` but read from
 * the snapshot, so a DataTab page load issues one HTTP call
 * instead of two.
 */
export function useUiSnapshotPending() {
  const q = useUiSnapshot();
  return {
    ...q,
    data: q.data?.pending_counts,
  } as typeof q & { data: UiSnapshotPending | undefined };
}

/** Convenience selector: extract only the tickers array. */
export function useUiSnapshotTickers() {
  const q = useUiSnapshot();
  return {
    ...q,
    data: q.data?.tickers,
  } as typeof q & { data: Ticker[] | undefined };
}
