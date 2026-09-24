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
  useStaleBreakdown,
} from '@features/backfill/hooks';

// PR #130 (2026-09-24): format pipeline-age hours into a friendly
// relative-time label for the DataTab. Returns "—" if the chain
// has never finished that phase (None from the API). Once we have
// data, "2ч 13м" / "1д 4ч" tells the operator at a glance whether
// the sweep ran today or a couple days ago.
function formatPipelineAge(hours: number | null): string {
  if (hours === null || hours === undefined) return '—';
  if (hours < 1) {
    return `${Math.max(1, Math.round(hours * 60))}м`;
  }
  if (hours < 24) {
    const h = Math.floor(hours);
    const m = Math.round((hours - h) * 60);
    return m === 0 ? `${h}ч` : `${h}ч ${m}м`;
  }
  const days = Math.floor(hours / 24);
  const h = Math.round(hours - days * 24);
  return h === 0 ? `${days}д` : `${days}д ${h}ч`;
}

// Stale indicator colour: red-tinged if either buckets hold a real
// number of items, neutral otherwise. Green means everything is
// fresh; red means it's high time to look at the worker logs.
function staleToneClass(hasStale1d: number, hasStale2d: number): string {
  if (hasStale2d > 0) return 'text-red-400';
  if (hasStale1d > 0) return 'text-amber-400';
  return 'text-green-400';
}

export function DataTab() {
  const { data: status, isLoading: statusLoading } = useBackfillStatus();
  const { data: pending } = usePendingCount();
  const { data: tickers, isLoading: tickersLoading } = useTickers();
  // PR #130 (2026-09-24): pull the multi-level stale breakdown so
  // we can render "устаревшие (>1 день)" — the operator asked for
  // this bucket specifically after the single "stale" count of 4
  // hid ~1000 figis that lag yesterday.
  const { data: staleBreakdown } = useStaleBreakdown();
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

  // When data isn't loaded yet, every KPI block must show the same
  // placeholder (`…`) — operators need a uniform signal to know
  // they're looking at a loading state, not a real zero count.
  const ready = !statusLoading && !tickersLoading && status != null && tickers != null;

  const totalBars = ready ? status!.total_bars : null;
  const tickersCount = ready ? tickers!.length : 0;
  const totalGaps = ready ? tickers!.reduce((s, t) => s + t.gaps, 0) : null;
  // The earliest "first bar" across all tickers. We display only
  // the START year because Tinkoff investAPI sandbox returns ~5
  // years of history regardless of instrument listing date —
  // showing start–end as a range misleads operators into thinking
  // we have e.g. 5 years of SBER when actually SBER is listed since
  // 1996 and we just don't have older bars.
  //
  // Skip empty `firstDate` values: the backend serialises zero-bar
  // tradable figis as `firstDate: ""` so the UI's per-ticker
  // completeness formula can detect them, but `"" < "2013-..."` is
  // true in JS so a naive min-reduce would pick the empty string
  // and the period block would render "…" forever.
  const firstDate = ready
    ? tickers!
        .filter((t) => t.firstDate && t.firstDate.length > 0)
        .reduce<string | null>(
          (acc, t) => (acc === null || t.firstDate < acc ? t.firstDate : acc),
          null,
        )
    : null;
  const completeness = (() => {
    if (!ready || tickersCount === 0) return null;
    // Per-ticker completeness: for each ticker, days_present /
    // ticker_span. Average across tickers. This avoids double-counting
    // gaps across tickers (the previous global-span / sum-gaps formula
    // showed 0% when sum_gaps > global_span, e.g. 73016 gaps in 1861
    // days).
    //
    // Zero-bar figis (no rows in `bars`, returned by /api/tickers
    // for tradable instruments that have never been backfilled)
    // must contribute 0% to the average — otherwise the UI lies
    // about Полнота by silently dropping them from the denominator.
    const perTicker = tickers!.map((t) => {
      if (t.bars === 0) return 0;
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
            Ежедневно в 23:00 МСК
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
              {totalBars != null ? totalBars.toLocaleString('ru') : '…'}
            </p>
          </div>
          <div>
            <p className="text-xs text-[var(--muted-foreground)] uppercase tracking-wide">
              Период
            </p>
            <p className="font-mono text-base mt-1 whitespace-nowrap" title="Tinkoff investAPI возвращает только ~5 лет истории баров вне зависимости от даты листинга инструмента">
              {firstDate
                ? `с ${firstDate.slice(0, 4)}`
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
              {totalGaps != null ? `${totalGaps.toLocaleString('ru')} дн` : '…'}
            </p>
          </div>
        </div>
      </section>

      {/* Section 2 — Backfill queue + multi-level stale (read+write) */}
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

        {/* PR #130: multi-level stale breakdown — the numbers the
            operator actually wants. Rendered as a three-up grid next
            to the queue counts so the contrast between "4 stale" and
            "1008 stale" is visible without drill-down. */}
        {staleBreakdown && (
          <div
            data-testid="data-stale-breakdown"
            className="rounded border border-[var(--border)] p-3 space-y-2"
          >
            <p className="text-xs text-[var(--muted-foreground)] uppercase tracking-wide">
              Устаревание баров (по состоянию на {staleBreakdown.as_of})
            </p>
            <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
              <div>
                <p className="text-[var(--muted-foreground)] uppercase tracking-wide">
                  Свежие
                </p>
                <p
                  className={
                    'font-mono text-base mt-1 ' +
                    staleToneClass(
                      staleBreakdown.bars.stale_more_than_1_day,
                      staleBreakdown.bars.stale_more_than_2_days,
                    )
                  }
                  data-testid="bars-fresh"
                >
                  {staleBreakdown.bars.fresh_or_today}
                </p>
                <p className="text-[10px] text-[var(--muted-foreground)] mt-0.5">
                  сегодня / вчера
                </p>
              </div>
              <div>
                <p className="text-[var(--muted-foreground)] uppercase tracking-wide">
                  Устаревшие (&gt;1 дн)
                </p>
                <p
                  className={
                    'font-mono text-base mt-1 ' +
                    staleToneClass(
                      staleBreakdown.bars.stale_more_than_1_day,
                      staleBreakdown.bars.stale_more_than_2_days,
                    )
                  }
                  data-testid="bars-stale-1d"
                >
                  {staleBreakdown.bars.stale_more_than_1_day}
                </p>
                <p className="text-[10px] text-[var(--muted-foreground)] mt-0.5">
                  отстают на 1 день
                </p>
              </div>
              <div>
                <p className="text-[var(--muted-foreground)] uppercase tracking-wide">
                  Устаревшие (&gt;2 дн)
                </p>
                <p
                  className={
                    'font-mono text-base mt-1 ' +
                    staleToneClass(
                      staleBreakdown.bars.stale_more_than_1_day,
                      staleBreakdown.bars.stale_more_than_2_days,
                    )
                  }
                  data-testid="bars-stale-2d"
                >
                  {staleBreakdown.bars.stale_more_than_2_days}
                </p>
                <p className="text-[10px] text-[var(--muted-foreground)] mt-0.5">
                  2+ дня без обновления
                </p>
              </div>
              <div>
                <p className="text-[var(--muted-foreground)] uppercase tracking-wide">
                  Без баров
                </p>
                <p className="font-mono text-base mt-1 text-amber-400" data-testid="bars-no-bars">
                  {staleBreakdown.bars.no_bars_ever}
                </p>
                <p className="text-[10px] text-[var(--muted-foreground)] mt-0.5">
                  нет данных вовсе
                </p>
              </div>
            </div>

            {/* PR #130: corporate-actions / dividends pipeline age.
                The previous UI only had the generic "freshness" flag
                from /admin/data-pipeline/status which was hidden in a
                folded block. The operator screen should call out when
                corporate actions last ran in plain language so we
                don't need to dig through /admin/data-pipeline/status
                logs to answer "shouldn't this be running on its own?". */}
            <div className="pt-2 border-t border-[var(--border)] grid grid-cols-2 gap-2 text-xs">
              <div>
                <p className="text-[var(--muted-foreground)] uppercase tracking-wide">
                  Корп. действия
                </p>
                <p
                  className="font-mono text-base mt-1"
                  data-testid="pipeline-age-corp-actions"
                >
                  {formatPipelineAge(
                    staleBreakdown.pipeline_age_hours.corporate_actions,
                  )}
                </p>
                <p className="text-[10px] text-[var(--muted-foreground)] mt-0.5">
                  с последней фазы
                </p>
              </div>
              <div>
                <p className="text-[var(--muted-foreground)] uppercase tracking-wide">
                  Дивиденды
                </p>
                <p
                  className="font-mono text-base mt-1"
                  data-testid="pipeline-age-dividends"
                >
                  {formatPipelineAge(
                    staleBreakdown.pipeline_age_hours.dividends,
                  )}
                </p>
                <p className="text-[10px] text-[var(--muted-foreground)] mt-0.5">
                  с последней фазы
                </p>
              </div>
            </div>
          </div>
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
            ? `Это удалит все строки instrument_metadata, поэтому следующий запланированный запуск (и любой последующий ручной триггер) перезагрузит всю историю для всех ${pending.total} инструментов. Используйте это только после корпоративного события, изменившего серию, или если подозреваете, что бары на диске повреждены. Плановое обслуживание автоматическое — ежедневный запуск в 23:00 МСК ловит новые и устаревшие тикеры без ручного вмешательства.`
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
