import { useQuery } from '@tanstack/react-query';
import { api } from '@lib/api';

/**
 * Per-ticker data-quality drilldown tab (issue #2).
 *
 * Renders the HealthReport the daily guardian computes for one ticker.
 * The backend endpoint is GET /api/data-quality/{symbol}; response
 * shape lives in apps/api/src/algotrader_api/routes/data_quality.py
 * (_report_to_dict).
 */

export type HealthIssue =
  | 'missing-recent-days'
  | 'sparse-history'
  | 'has-gaps'
  | 'rate-limited-failures';

export type HealthReport = {
  figi: string;
  ticker: string;
  health_score: number; // 0-100
  issues: HealthIssue[];
  first_bar: string | null; // YYYY-MM-DD
  last_bar: string | null;
  actual_bars: number;
  expected_bars: number;
  recent_gaps: string[];
  recent_failures: { ts: string; level: string; message: string }[];
};

export function useDataQuality(ticker: string) {
  return useQuery<HealthReport>({
    queryKey: ['data-quality', ticker],
    queryFn: () => api<HealthReport>(`/data-quality/${ticker}`),
    enabled: Boolean(ticker),
    // Re-fetch every minute so the operator sees the latest
    // guardian pass without having to reload the page.
    refetchInterval: 60_000,
  });
}

export function DataQualityTab({ ticker }: { ticker: string }) {
  const query = useDataQuality(ticker);

  // Treat 404s as "no data for this ticker" — that's the documented
  // backend contract for unknown symbols (see routes/data_quality.py).
  // Only 5xx / network errors are rendered as the error state with
  // a refetch affordance. The user-facing audit (issue #2) wants
  // operators to see the health score when available, see a clean
  // "no data" placeholder when the symbol isn't tracked, and only
  // treat server errors as actionable.
  const is404 =
    query.isError &&
    // TanStack Query exposes the underlying error via query.error;
    // we duck-type the HTTP status check to avoid coupling to
    // tanstack's exact error wrapper.
    (query.error as unknown as { status?: number; response?: { status?: number } })
      ?.status === 404 ||
    (query.error as unknown as { response?: { status?: number } })
      ?.response?.status === 404;
  const showError = query.isError && !is404;

  if (query.isLoading) {
    return (
      <div className="p-6 text-text-muted" data-testid="data-quality-loading">
        Загрузка…
      </div>
    );
  }

  if (showError) {
    return (
      <div className="p-6" data-testid="data-quality-error">
        <p className="text-danger">
          Не удалось получить отчёт по {ticker}.
        </p>
        <button
          type="button"
          onClick={() => query.refetch()}
          className="mt-2 text-xs underline text-text-muted hover:text-text"
          data-testid="data-quality-refetch"
        >
          Повторить запрос
        </button>
      </div>
    );
  }

  const report = query.data;
  if (!report) {
    return (
      <div
        className="p-6 text-text-muted"
        data-testid="data-quality-empty"
      >
        Нет данных по тикеру {ticker}. Возможно, инструмент ещё не
        обработан гардианом.
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-baseline gap-3">
        <span
          className="text-3xl font-mono"
          data-testid="data-quality-score"
        >
          {report.health_score}
        </span>
        <span className="text-text-muted">/ 100 — здоровье данных</span>
      </div>

      <div data-testid="data-quality-issues">
        <p className="text-xs uppercase text-text-muted mb-1">Проблемы</p>
        {report.issues.length === 0 ? (
          <p className="text-text-muted">Нет — данные в порядке.</p>
        ) : (
          <ul className="space-y-1">
            {report.issues.map((issue) => (
              <li key={issue} className="text-sm text-danger">
                {issue}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
        <div>
          <p className="text-xs uppercase text-text-muted">FIGI</p>
          <p className="font-mono">{report.figi}</p>
        </div>
        <div>
          <p className="text-xs uppercase text-text-muted">Тикер</p>
          <p>{report.ticker}</p>
        </div>
        <div>
          <p className="text-xs uppercase text-text-muted">Первая свеча</p>
          <p>{report.first_bar ?? '—'}</p>
        </div>
        <div>
          <p className="text-xs uppercase text-text-muted">Последняя свеча</p>
          <p>{report.last_bar ?? '—'}</p>
        </div>
        <div>
          <p className="text-xs uppercase text-text-muted">Баров на диске</p>
          <p className="font-mono">{report.actual_bars}</p>
        </div>
        <div>
          <p className="text-xs uppercase text-text-muted">Ожидалось</p>
          <p className="font-mono">{report.expected_bars}</p>
        </div>
      </div>

      {report.recent_gaps.length > 0 && (
        <div>
          <p className="text-xs uppercase text-text-muted mb-1">
            Последние пропуски
          </p>
          <p className="text-sm font-mono">
            {report.recent_gaps.join(', ')}
          </p>
        </div>
      )}

      {report.recent_failures.length > 0 && (
        <div>
          <p className="text-xs uppercase text-text-muted mb-1">
            Последние ошибки ingestion
          </p>
          <ul className="space-y-1">
            {report.recent_failures.map((f, i) => (
              <li key={i} className="text-sm text-text-muted font-mono">
                {f.ts} [{f.level}] {f.message}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
