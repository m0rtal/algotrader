import { useRegime, useTickers } from '@lib/hooks';
import { useUiStore } from '@stores/uiStore';

export function Sidebar() {
  const { data: regime } = useRegime();
  const { data: tickers } = useTickers();
  const openTicker = useUiStore((s) => s.openTicker);

  if (!regime) return null;

  return (
    <div className="p-4 space-y-3.5">
      <section className="bg-surface-2 border border-border rounded-md p-3">
        <h3 className="text-[10px] uppercase tracking-wider text-text-dim font-semibold">Режим рынка</h3>
        <div className="flex items-center gap-2.5 mt-2">
          <span className="w-2.5 h-2.5 rounded-full bg-green shadow-[0_0_8px_var(--color-green)]" />
          <span className="font-semibold text-sm capitalize">{regime.state === 'trend' ? 'Up-Trend' : regime.state}</span>
          <span className="text-[11px] text-text-muted ml-auto mono">conf {regime.confidence}</span>
        </div>
        <div className="flex flex-col gap-1.5 mt-2.5">
          <Meter label="IMOEX" value={`${regime.imoexChange > 0 ? '+' : ''}${regime.imoexChange}%`} pct={62} color="green" />
          <Meter label="Vol (20д)" value={`${regime.volatility20d}%`} pct={34} color="accent" />
          <Meter label="Breadth" value={`${Math.round(regime.breadth * 100)}%`} pct={71} color="green" />
        </div>
        <div className="text-[11px] text-text-muted mt-2.5 pt-2.5 border-t border-border-soft">
          HMM 3-сост. · с {regime.sinceDate}
        </div>
      </section>

      <div>
        <h3 className="text-[10px] uppercase tracking-wider text-text-dim font-semibold mb-2.5">
          Universe · кликни тикер
        </h3>
        <div className="grid grid-cols-2 gap-2 mb-2.5">
          <Stat label="Активных" value={tickers?.length ?? 47} />
          <Stat label="С фильтром" value={23} />
        </div>
        <div className="max-h-[240px] overflow-y-auto">
          {(tickers ?? []).map((t) => (
            <button
              key={t.symbol}
              type="button"
              onClick={() => openTicker(t.symbol)}
              className="w-full flex items-center justify-between text-xs py-1.5 px-2 border-b border-border-soft hover:bg-surface-2 text-left"
            >
              <span className="mono font-medium">{t.symbol}</span>
              <span className="text-text-dim text-[10px] mono">{t.bars.toLocaleString('ru')}</span>
              <span className="mono text-text-muted">{t.price.toLocaleString('ru')}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function Meter({ label, value, pct, color }: { label: string; value: string; pct: number; color: 'green' | 'accent' }) {
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="text-text-muted w-[70px]">{label}</span>
      <span className="flex-1 h-1 bg-border-soft rounded-sm overflow-hidden">
        <span
          className="block h-full rounded-sm"
          style={{ width: `${pct}%`, background: color === 'green' ? 'var(--color-green)' : 'var(--color-accent)' }}
        />
      </span>
      <span className="mono w-[50px] text-right text-text">{value}</span>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-surface-2 rounded p-2">
      <div className="text-[10px] text-text-dim uppercase">{label}</div>
      <div className="mono text-[15px] mt-0.5">{value}</div>
    </div>
  );
}
