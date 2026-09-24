import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@lib/api';

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

export type Pending = {
  new: number;
  stale: number;
  up_to_date: number;
  error: number;
  total: number;
};

export function usePendingCount(refetchInterval = 60_000) {
  return useQuery<Pending>({
    queryKey: ['backfill-pending'],
    queryFn: () => api<Pending>('/admin/backfill/pending'),
    refetchInterval,
  });
}

// PR #130 (2026-09-24): multi-level stale breakdown.
//
// Replaces the single ``pending.stale`` (2-day threshold) with three
// buckets the operator actually cares about:
// - ``fresh_or_today``: today-or-yesterday bars; no action needed
// - ``stale_more_than_1_day``: bars from the day before yesterday
//   (this is the bucket the user asked for — anything that lags
//   yesterday gets a callout)
// - ``stale_more_than_2_days``: figis that haven't moved despite
//   multiple worker cycles
//
// Plus pipeline age for corporate_actions and dividends, so the
// operator can see when the chain last reached those phases without
// grepping the logs.
export type StaleBreakdown = {
  as_of: string;
  yesterday: string;
  bars: {
    fresh_or_today: number;
    stale_more_than_1_day: number;
    stale_more_than_2_days: number;
    no_bars_ever: number;
    tradable_total: number;
    samples_stale_1d: Array<[string, string, string]>;
    samples_stale_2d: Array<[string, string, string]>;
    samples_no_bars: Array<[string, string, string]>;
  };
  pipeline_age_hours: {
    corporate_actions: number | null;
    dividends: number | null;
  };
};

export function useStaleBreakdown(refetchInterval = 60_000) {
  return useQuery<StaleBreakdown>({
    queryKey: ['admin-stale-breakdown'],
    queryFn: () => api<StaleBreakdown>('/admin/data-stale-breakdown'),
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
