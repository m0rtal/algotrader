import { useState } from 'react';
import { useTickers } from '@lib/hooks';
import { useUiStore } from '@stores/uiStore';
import { ConfirmDialog } from '@features/settings/ConfirmDialog';
import {
  useBackfillStatus,
  usePendingCount,
  useStartBackfill,
  useStopBackfill,
  useForceReset,
} from '@features/backfill/hooks';

export function DataTab() {
  const { data: status } = useBackfillStatus();
  const { data: pending } = usePendingCount();
  const { data: tickers } = useTickers();
  const start = useStartBackfill();
  const stop = useStopBackfill();
  const reset = useForceReset();
  const openTicker = useUiStore((s) => s.openTicker);
  const [showResetConfirm, setShowResetConfirm] = useState(false);

  const running =
    status?.state === 'running' ||
    status?.state === 'backfilling' ||
    status?.state === 'discovering' ||
    status?.state === 'stopping';

  const pct =
    status && status.tickers_total > 0
      ? Math.round((status.tickers_done / status.tickers_total) * 100)
      : 0;

  const totalBars = status?.total_bars ?? 0;
  const tickersCount = tickers?.length ?? 0;
  const totalGaps = tickers?.reduce((s, t) => s + t.gaps, 0) ?? 0;
  const firstDate = tickers?.reduce<string | null>(
    (acc, t) => (acc === null || t.firstDate < acc ? t.firstDate : acc),
    null,
  );
  const lastDate = tickers?.reduce<string | null>(
    (acc, t) => (acc === null || t.lastDate > acc ? t.lastDate : acc),
    null,
  );
  const completeness = (() => {
    if (!tickers || tickersCount === 0) return null;
    // Per-ticker completeness: for each ticker, days_present /
    // ticker_span. Average across tickers. This avoids double-counting
    // gaps across tickers (the previous global-span / sum-gaps formula
    // showed 0% when sum_gaps > global_span, e.g. 73016 gaps in 1861
    // days).
    const perTicker = tickers.map((t) => {
      const startMs = new Date(t.firstDate).getTime();
      const endMs = new Date(t.lastDate).getTime();
      const span = Math.max(1, Math.round((endMs - startMs) / 86_400_000));
      const daysPresent = Math.max(0, span - t.gaps);
      return daysPresent / span;
    });
    const avg = perTicker.reduce((a, b) => a + b, 0) / perTicker.length;
    return `${(avg * 100).toFixed(1)}%`;
  })();

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Section 1 — System state (read) */}
      <section
        data-testid="data-status"
        className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4 space-y-4"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Данные</h2>
          <span className="text-xs text-[var(--muted-foreground)]">
            Ежедневно в 02:00 МСК
          </span>
        </div>
        <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-5">
          <div>
            <p className="text-xs text-[var(--muted-foreground)] uppercase tracking-wide">
              Состояние
            </p>
            <p className="font-mono text-base mt-1">{status?.state ?? '…'}</p>
          </div>
          <div>
            <p className="text-xs text-[var(--muted-foreground)] uppercase tracking-wide">
              Баров на диске
            </p>
            <p className="font-mono text-base mt-1">
              {totalBars.toLocaleString('ru')}
            </p>
          </div>
          <div>
            <p className="text-xs text-[var(--muted-foreground)] uppercase tracking-wide">
              Период
            </p>
            <p className="font-mono text-base mt-1 whitespace-nowrap">
              {firstDate && lastDate
                ? `${firstDate.slice(0, 7)} → ${lastDate.slice(0, 7)}`
                : '…'}
            </p>
          </div>
          <div>
            <p className="text-xs text-[var(--muted-foreground)] uppercase tracking-wide">
              Полнота
            </p>
            <p className="font-mono text-base mt-1">{completeness ?? '…'}</p>
          </div>
          <div>
            <p className="text-xs text-[var(--muted-foreground)] uppercase tracking-wide">
              Гэпы
            </p>
            <p className="font-mono text-base mt-1">
              {totalGaps > 0 ? `${totalGaps.toLocaleString('ru')} дн` : '—'}
            </p>
          </div>
        </div>
      </section>

      {/* Section 2 — Backfill queue (write) */}
      <section
        data-testid="data-queue"
        className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4 space-y-4"
      >
        <h3 className="text-sm font-medium mb-2">Очередь бэкфилла</h3>
        {pending && pending.new + pending.stale + pending.error === 0 ? (
          <p className="text-sm text-green-400">
            Все {pending.total} тикеров актуальны. Планировщику делать нечего.
          </p>
        ) : (
          <>
            <p className="text-sm text-[var(--muted-foreground)] mb-2">
              {pending
                ? `${pending.new + pending.stale + pending.error} из ${pending.total} тикеров требуют обновления.`
                : 'Считаю…'}
            </p>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div>
                <p className="text-xs text-[var(--muted-foreground)] uppercase tracking-wide">
                  Новые
                </p>
                <p className="font-mono text-base mt-1">{pending?.new ?? 0}</p>
              </div>
              <div>
                <p className="text-xs text-[var(--muted-foreground)] uppercase tracking-wide">
                  Устаревшие (&gt;2 дн)
                </p>
                <p className="font-mono text-base mt-1">{pending?.stale ?? 0}</p>
              </div>
              <div>
                <p className="text-xs text-[var(--muted-foreground)] uppercase tracking-wide">
                  С ошибками
                </p>
                <p className="font-mono text-base mt-1">{pending?.error ?? 0}</p>
              </div>
            </div>
          </>
        )}

        {/* Progress bar — visible only while a run is in progress. */}
        {status && status.tickers_total > 0 && (
          <div data-testid="data-progress" aria-live="polite">
            <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)] mb-1">
              <span>Активный запуск</span>
              <span>
                {status.tickers_done} / {status.tickers_total} тикеров ({pct}%)
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
            Запустить бэкфилл
          </button>
          <button
            type="button"
            disabled={!running}
            onClick={() => stop.mutate()}
            className="rounded border border-[var(--border)] px-3 py-1.5 text-sm disabled:opacity-50"
          >
            Остановить
          </button>
          <button
            type="button"
            disabled={reset.isPending}
            onClick={() => setShowResetConfirm(true)}
            className="rounded border border-[var(--border)] px-3 py-1.5 text-sm text-red-400 disabled:opacity-50"
          >
            Сбросить метаданные
          </button>
          {reset.isError && (
            <p className="text-sm text-red-400 self-center">
              {reset.error?.message ?? 'unknown'}
            </p>
          )}
        </div>
      </section>

      {/* Section 3 — Ticker inventory */}
      <section
        data-testid="data-tickers"
        className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4"
      >
        <h3 className="text-sm font-medium mb-3">Тикеры</h3>
        <div className="max-h-[360px] overflow-auto">
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-[var(--card)]">
              <tr>
                <th className="text-left p-2 text-[10px] uppercase tracking-wider text-text-dim font-semibold">
                  Тикер
                </th>
                <th className="text-right p-2 text-[10px] uppercase tracking-wider text-text-dim font-semibold">
                  Бары
                </th>
                <th className="text-right p-2 text-[10px] uppercase tracking-wider text-text-dim font-semibold">
                  Размер
                </th>
                <th className="text-right p-2 text-[10px] uppercase tracking-wider text-text-dim font-semibold">
                  Период
                </th>
                <th className="text-right p-2 text-[10px] uppercase tracking-wider text-text-dim font-semibold">
                  Гэпы
                </th>
              </tr>
            </thead>
            <tbody>
              {(tickers ?? [])
                .slice()
                .sort((a, b) => b.bars - a.bars)
                .map((t) => (
                  <tr
                    key={t.symbol}
                    onClick={() => openTicker(t.symbol)}
                    className="border-b border-border-soft hover:bg-[var(--muted)]/30 cursor-pointer"
                    data-testid={`data-row-${t.symbol}`}
                  >
                    <td className="p-2 mono font-medium">{t.symbol}</td>
                    <td className="p-2 mono text-right">
                      {t.bars.toLocaleString('ru')}
                    </td>
                    <td className="p-2 mono text-right text-[var(--muted-foreground)]">
                      {(t.fileSize / 1024).toFixed(1)} KB
                    </td>
                    <td className="p-2 mono text-right text-[var(--muted-foreground)]">
                      {t.firstDate.slice(0, 7)} → {t.lastDate.slice(0, 7)}
                    </td>
                    <td className="p-2 mono text-right">
                      <span
                        className={
                          t.gaps > 0
                            ? 'text-amber'
                            : 'text-[var(--muted-foreground)]'
                        }
                      >
                        {t.gaps}
                      </span>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        <p className="text-[11px] text-text-muted mt-2">
          Кликните тикер для просмотра баров и деталей.
        </p>
      </section>

      <ConfirmDialog
        open={showResetConfirm && !!pending}
        title="Сбросить метаданные и перезагрузить всё?"
        body={
          pending
            ? `Это удалит все строки instrument_metadata, поэтому следующий запланированный запуск (и любой последующий ручной триггер) перезагрузит всю историю для всех ${pending.total} инструментов. Используйте это только после корпоративного события, изменившего серию, или если подозреваете, что бары на диске повреждены. Плановое обслуживание автоматическое — ежедневный запуск в 02:00 МСК ловит новые и устаревшие тикеры без ручного вмешательства.`
            : 'Загружаю количество инструментов…'
        }
        confirmLabel="Сбросить метаданные"
        cancelLabel="Отмена"
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
