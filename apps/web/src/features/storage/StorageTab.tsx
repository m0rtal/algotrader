import { useTickers } from '@lib/hooks';
import { formatPct } from '@lib/format';
import { useUiStore } from '@stores/uiStore';
import { Sparkline } from '@components/charts/Sparkline';

export function StorageTab() {
  const { data, isLoading, isError } = useTickers();
  const openTicker = useUiStore((s) => s.openTicker);
  if (isLoading) return <div className="p-4 text-text-muted">Загрузка…</div>;
  if (isError) return <div className="p-4 text-red">Ошибка загрузки</div>;
  if (!data || data.length === 0) return <div className="p-4 text-text-muted">Нет тикеров</div>;
  const totalBars = data.reduce((sum, t) => sum + t.bars, 0);
  const totalSize = data.reduce((sum, t) => sum + t.fileSize, 0);
  return (
    <div className="p-4">
      <div className="grid grid-cols-4 gap-2 mb-3.5">
        <Card
          label="Всего баров"
          value={totalBars.toLocaleString('ru')}
          sub={`${data.length} тикеров × 5 лет`}
        />
        <Card
          label="Размер на диске"
          value={`${(totalSize / 1024 / 1024).toFixed(1)} MB`}
          sub="parquet сжатый"
        />
        <Card label="Период" value="2021-09 → сегодня" sub="1 247 торговых дней" />
        <Card
          label="Полнота данных"
          value="99.2%"
          sub={`гэпы: ${data.reduce((s, t) => s + t.gaps, 0)} дней`}
        />
      </div>
      <div className="text-[10px] uppercase tracking-wider text-text-dim font-semibold mb-2.5">
        По тикерам · кликни для деталей
      </div>
      <div className="max-h-[360px] overflow-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="bg-surface">
              {['Тикер', 'Баров', 'Период', 'Размер', 'Гэпы', 'Last update', 'Δ 5д', 'Spark'].map(
                (h) => (
                  <th
                    key={h}
                    className="text-left px-2 py-1.5 font-medium text-text-muted text-[9px] uppercase tracking-wider border-b border-border"
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {data.map((t) => (
              <tr
                key={t.symbol}
                className="hover:bg-surface-2 cursor-pointer"
                onClick={() => openTicker(t.symbol)}
              >
                <td className="px-2 py-1.5 mono border-b border-border-soft font-medium">
                  {t.symbol}
                </td>
                <td className="px-2 py-1.5 mono border-b border-border-soft">
                  {t.bars.toLocaleString('ru')}
                </td>
                <td className="px-2 py-1.5 mono border-b border-border-soft">
                  {t.firstDate} → {t.lastDate}
                </td>
                <td className="px-2 py-1.5 mono border-b border-border-soft">
                  {Math.round(t.fileSize / 1024)} KB
                </td>
                <td
                  className={`px-2 py-1.5 mono border-b border-border-soft ${
                    t.gaps > 0 ? 'text-amber' : 'text-text-muted'
                  }`}
                >
                  {t.gaps}
                </td>
                <td className="px-2 py-1.5 mono border-b border-border-soft text-text-muted">
                  2026-09-06 19:31
                </td>
                <td className="px-2 py-1.5 mono border-b border-border-soft">
                  {formatPct(((t.price - 100) / 100) * 0.05)}
                </td>
                <td className="px-2 py-1.5 border-b border-border-soft">
                  <Sparkline symbol={t.symbol} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Card({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="bg-surface-2 border border-border rounded-md px-3 py-2.5">
      <div className="text-[10px] text-text-dim uppercase tracking-wider">{label}</div>
      <div className="mono text-[18px] mt-1">{value}</div>
      <div className="text-[11px] text-text-muted mono mt-0.5">{sub}</div>
    </div>
  );
}
