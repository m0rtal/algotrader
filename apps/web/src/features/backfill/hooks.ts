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
