import { useTrades } from '@lib/hooks';
import { formatDateTime, formatNum, formatRUB } from '@lib/format';

export function TradesTab() {
  const { data, isLoading } = useTrades();
  if (isLoading || !data) return <div className="p-4 text-text-muted">Загрузка…</div>;
  return (
    <div className="p-4 overflow-x-auto">
      <table className="w-full text-xs min-w-[600px]">
        <thead>
          <tr className="bg-surface">
            {['Время', 'Тикер', 'Сторона', 'Кол-во', 'Цена', 'Сумма', 'P&L', 'Стратегия'].map((h) => (
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
          {data.map((t) => (
            <tr key={t.id} className="hover:bg-surface-2">
              <td className="px-3 py-2 mono border-b border-border-soft text-text-muted">
                {formatDateTime(t.ts)}
              </td>
              <td className="px-3 py-2 mono border-b border-border-soft">{t.symbol}</td>
              <td
                className={`px-3 py-2 mono border-b border-border-soft font-semibold ${
                  t.side === 'buy' ? 'text-green' : 'text-red'
                }`}
              >
                {t.side.toUpperCase()}
              </td>
              <td className="px-3 py-2 mono border-b border-border-soft">{t.qty} лот</td>
              <td className="px-3 py-2 mono border-b border-border-soft">{formatNum(t.price)}</td>
              <td className="px-3 py-2 mono border-b border-border-soft">{formatRUB(t.amount)}</td>
              <td
                className={`px-3 py-2 mono border-b border-border-soft ${
                  t.pnl == null
                    ? 'text-text-muted'
                    : t.pnl > 0
                      ? 'text-green'
                      : t.pnl < 0
                        ? 'text-red'
                        : 'text-text-muted'
                }`}
              >
                {t.pnl == null ? '—' : formatRUB(t.pnl, { sign: true })}
              </td>
              <td className="px-3 py-2 mono border-b border-border-soft">{t.strategy}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
