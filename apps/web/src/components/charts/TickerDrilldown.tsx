import { useEffect, useId, useRef } from 'react';
import { useUiStore } from '@stores/uiStore';
import { useBars, useTickers } from '@lib/hooks';
import { formatPct } from '@lib/format';
import { EquityCurve } from '@components/charts/EquityCurve';

export function TickerDrilldown() {
  const symbol = useUiStore((s) => s.selectedTicker);
  const close = useUiStore((s) => s.closeTicker);
  const { data: tickers } = useTickers();
  const { data: bars, isLoading } = useBars(symbol);
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!symbol) return;
    // Move focus into the panel on open; restore on close.
    const panel = panelRef.current;
    const focusable = panel?.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    previousFocus.current = document.activeElement as HTMLElement | null;
    focusable?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        /* v8 ignore next */
        close();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      previousFocus.current?.focus();
    };
  }, [symbol, close]);

  if (!symbol) return null;

  const ticker = tickers?.find((t) => t.symbol === symbol);
  const closes = bars?.bars.map((b: { close: number }) => b.close) ?? [];
  const change5 =
    closes.length >= 6
      ? ((closes[closes.length - 1]! - closes[closes.length - 6]!) / closes[closes.length - 6]!) *
        100
      : 0;
  const changeAll =
    closes.length >= 2 ? ((closes[closes.length - 1]! - closes[0]!) / closes[0]!) * 100 : 0;

  return (
    // Backdrop is a click target only; Escape handling lives on window
    // in the useEffect above. role="presentation" tells AT to ignore
    // the div while the inner role="dialog" carries the semantics.
    <div
      role="presentation"
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="bg-surface border border-border rounded-none sm:rounded-lg p-4 sm:p-5 w-full max-w-full sm:max-w-[640px] md:max-w-[800px] max-h-screen sm:max-h-[80vh] overflow-y-auto"
      >
        <div className="flex items-center justify-between mb-4">
          <h2 id={titleId} className="text-base font-semibold mono">
            {symbol} · {ticker?.name ?? ''}
          </h2>
          <button
            type="button"
            onClick={close}
            className="text-text-muted hover:text-text text-lg px-2 py-1"
          >
            ✕
          </button>
        </div>
        <div className="grid grid-cols-4 gap-2 mb-4">
          <Stat label="Баров" value={bars?.count.toLocaleString('ru') ?? '—'} />
          <Stat label="Период" value={bars ? `${bars.first} → ${bars.last}` : '—'} small />
          <Stat label="Размер" value={ticker ? `${Math.round(ticker.fileSize / 1024)} KB` : '—'} />
          <Stat
            label="Гэпы"
            value={ticker?.gaps.toString() ?? '—'}
            tone={ticker && ticker.gaps > 0 ? 'warn' : undefined}
          />
          <Stat label="Цена" value={ticker?.price.toLocaleString('ru') ?? '—'} />
          <Stat label="Δ 5д" value={formatPct(change5)} tone={change5 >= 0 ? 'pos' : 'neg'} />
          <Stat label="Δ 30д" value={formatPct(changeAll)} tone={changeAll >= 0 ? 'pos' : 'neg'} />
          <Stat label="Сектор" value={ticker?.sector ?? '—'} small />
        </div>
        <div className="text-[10px] uppercase tracking-wider text-text-dim font-semibold mb-2.5">
          Бары · последние {closes.length} дней
        </div>
        <div className="bg-surface-2 border border-border rounded-md p-3 mb-3.5 h-[140px]">
          {isLoading ? (
            <div className="text-text-muted h-full flex items-center justify-center">Загрузка…</div>
          ) : closes.length > 0 ? (
            <EquityCurve data={closes} height={100} />
          ) : (
            /* v8 ignore next */
            <div className="text-text-muted h-full flex items-center justify-center">
              Нет данных
            </div>
          )}
        </div>
        <div className="text-[10px] uppercase tracking-wider text-text-dim font-semibold mb-2.5">
          Источник данных
        </div>
        <div className="text-xs text-text-muted mono space-y-1">
          <div>
            Путь: <span className="text-text">data/bars/{symbol}.parquet</span>
          </div>
          <div>
            Источник: <span className="text-text">MOEX ISS (iss.moex.com)</span>
          </div>
          <div>
            Формат: <span className="text-text">parquet (snappy)</span>
          </div>
          <div>
            Обновлено: <span className="text-text">2026-09-06 19:31:42 МСК</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
  small,
}: {
  label: string;
  value: string;
  tone?: 'pos' | 'neg' | 'warn';
  small?: boolean;
}) {
  const color =
    tone === 'pos'
      ? 'text-green'
      : tone === 'neg'
        ? 'text-red'
        : tone === 'warn'
          ? 'text-amber'
          : 'text-text';
  return (
    <div className="bg-surface-2 rounded p-2">
      <div className="text-[10px] text-text-dim uppercase">{label}</div>
      <div className={`mono mt-0.5 ${small ? 'text-[11px]' : 'text-[14px]'} ${color}`}>{value}</div>
    </div>
  );
}
