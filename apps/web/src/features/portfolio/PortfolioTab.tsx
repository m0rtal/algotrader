import { usePortfolio } from '@lib/hooks';
import { formatNum, formatRUB } from '@lib/format';

export function PortfolioTab() {
  const { data, isLoading } = usePortfolio();
  if (isLoading || !data) return <div className="p-4 text-text-muted">Загрузка…</div>;
  return (
    <div className="p-4 overflow-x-auto">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 mb-3.5 min-w-[600px]">
        <Card label="Свободно" value={formatRUB(data.cash)} sub={`${((data.cash / data.total) * 100).toFixed(1)}%`} />
        <Card
          label="В позициях"
          value={formatRUB(data.invested)}
          sub={`${((data.invested / data.total) * 100).toFixed(1)}%`}
        />
        <Card
          label="Позиций"
          value={String(data.positions.length)}
          sub={`длинных: ${data.longCount} / коротких: ${data.shortCount}`}
        />
        <Card
          label="Gross exposure"
          value={`${data.grossExposure.toFixed(2)}x`}
          sub={`net ${data.netExposure.toFixed(2)}x`}
        />
      </div>
      <table className="w-full text-xs min-w-[700px]">
        <thead>
          <tr className="bg-surface">
            {['Тикер', 'Сторона', 'Кол-во', 'Средняя', 'Цена', 'Стоимость', 'Доля', 'P&L'].map((h) => (
              <th
                key={h}
                className="text-left px-3 py-2 font-medium text-text-muted text-[10px] uppercase tracking-wider border-b border-border"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.positions.map((p) => (
            <tr key={p.symbol} className="hover:bg-surface-2">
              <td className="px-3 py-2 mono border-b border-border-soft font-medium">{p.symbol}</td>
              <td
                className={`px-3 py-2 mono border-b border-border-soft font-semibold ${
                  p.side === 'long' ? 'text-green' : 'text-red'
                }`}
              >
                {p.side.toUpperCase()}
              </td>
              <td className="px-3 py-2 mono border-b border-border-soft">{p.qty} лот</td>
              <td className="px-3 py-2 mono border-b border-border-soft">{formatNum(p.avgPrice)}</td>
              <td className="px-3 py-2 mono border-b border-border-soft">{formatNum(p.price)}</td>
              <td className="px-3 py-2 mono border-b border-border-soft">{formatRUB(p.value)}</td>
              <td className="px-3 py-2 mono border-b border-border-soft">
                <div className="flex items-center gap-2">
                  <span className="flex-1 h-1.5 bg-border-soft rounded-sm overflow-hidden">
                    <span
                      className="block h-full bg-accent"
                      style={{ width: `${p.weight * 100}%` }}
                    />
                  </span>
                  <span className="w-[50px] text-right">{(p.weight * 100).toFixed(1)}%</span>
                </div>
              </td>
              <td
                className={`px-3 py-2 mono border-b border-border-soft ${
                  p.pnl > 0 ? 'text-green' : p.pnl < 0 ? 'text-red' : 'text-text-muted'
                }`}
              >
                {p.pnl > 0 ? '+' : ''}
                {p.pnl} ₽
              </td>
            </tr>
          ))}
        </tbody>
      </table>
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
