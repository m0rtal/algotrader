import { useSignals } from '@lib/hooks';
import { formatNum, formatPct, formatTime } from '@lib/format';
import { useUiStore } from '@stores/uiStore';
import { EquityCurve } from '@components/charts/EquityCurve';

const EQUITY = [
  1111942, 1112842, 1113542, 1114042, 1115442, 1116442, 1117542, 1118442, 1119542, 1120442, 1121142,
  1121642, 1122342, 1122942, 1123742, 1124442, 1125342, 1126142, 1127142, 1127942, 1128542, 1129042,
  1129742, 1130442, 1131142, 1131642, 1132242, 1133042, 1133942, 1124380,
];

export function SignalsTab() {
  const { data, isLoading, isError } = useSignals();
  const openTicker = useUiStore((s) => s.openTicker);

  if (isLoading) return <div className="p-4 text-text-muted">Загрузка…</div>;
  if (isError) return <div className="p-4 text-red">Ошибка загрузки</div>;
  if (!data || data.length === 0) return <div className="p-4 text-text-muted">Нет сигналов</div>;

  return (
    <>
      <div className="p-4 overflow-x-auto">
        <table className="w-full text-xs min-w-[600px]">
          <thead>
            <tr className="bg-surface">
              {[
                { label: 'Тикер', hide: '' },
                { label: 'Сигнал', hide: '' },
                { label: 'Цена', hide: '' },
                { label: 'Прогноз 5д', hide: '' },
                { label: 'Уверенность', hide: '' },
                { label: 'Сила', hide: 'md:table-cell' },
                { label: 'Regime', hide: 'sm:table-cell' },
                { label: 'Объём', hide: 'lg:table-cell' },
                { label: 'Обновлён', hide: '' },
              ].map(
                (h) => (
                  <th
                    key={h.label}
                    className={`text-left px-2 py-2 font-medium text-text-muted text-[10px] uppercase tracking-wider border-b border-border whitespace-nowrap ${h.hide}`}
                  >
                    {h.label}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {data.map((s) => (
              <tr key={s.symbol} className="hover:bg-surface-2">
                <td className="px-2 py-2 mono border-b border-border-soft font-medium whitespace-nowrap">
                  <button type="button" onClick={() => openTicker(s.symbol)} className="hover:text-accent">
                    {s.symbol}
                  </button>
                </td>
                <td className="px-2 py-2 mono border-b border-border-soft whitespace-nowrap">
                  <span
                    className={`inline-block px-2 py-0.5 rounded-sm text-[10px] font-semibold uppercase ${
                      s.side === 'long'
                        ? 'bg-green-soft text-green'
                        : s.side === 'short'
                          ? 'bg-red-soft text-red'
                          : 'bg-text-soft text-text-muted'
                    }`}
                  >
                    {s.side}
                  </span>
                </td>
                <td className="px-2 py-2 mono border-b border-border-soft whitespace-nowrap">{formatNum(s.price)}</td>
                <td
                  className={`px-2 py-2 mono border-b border-border-soft whitespace-nowrap ${
                    s.forecast5d > 0 ? 'text-green' : s.forecast5d < 0 ? 'text-red' : 'text-text-muted'
                  }`}
                >
                  {formatPct(s.forecast5d)}
                </td>
                <td className="px-2 py-2 mono border-b border-border-soft whitespace-nowrap">
                  {s.confidence.toFixed(2)}
                </td>
                <td className="px-2 py-2 mono border-b border-border-soft hidden md:table-cell whitespace-nowrap">
                  <span className="inline-block w-10 h-1 bg-border-soft rounded-sm align-middle ml-1.5">
                    <span
                      className="block h-full bg-accent rounded-sm"
                      style={{ width: `${s.strength * 100}%` }}
                    />
                  </span>
                </td>
                <td className="px-2 py-2 mono border-b border-border-soft hidden sm:table-cell whitespace-nowrap">{s.regime}</td>
                <td className="px-2 py-2 mono border-b border-border-soft hidden lg:table-cell whitespace-nowrap">
                  {s.volume.toLocaleString('ru')} лот
                </td>
                <td className="px-2 py-2 mono border-b border-border-soft text-text-muted whitespace-nowrap">
                  {formatTime(s.updatedAt)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="px-4 pb-4">
        <div className="text-[10px] uppercase tracking-wider text-text-dim font-semibold mb-2.5">
          Equity Curve — 90 дней
        </div>
        <div className="h-[220px] bg-surface border border-border rounded-md p-3">
          <EquityCurve data={EQUITY} />
        </div>
      </div>
    </>
  );
}
