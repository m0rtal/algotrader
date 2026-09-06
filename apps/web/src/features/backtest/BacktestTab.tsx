import { useFolds } from '@lib/hooks';
import { formatPct } from '@lib/format';
import { EquityCurve } from '@components/charts/EquityCurve';

export function BacktestTab() {
  const { data, isLoading } = useFolds();
  if (isLoading || !data) return <div className="p-4 text-text-muted">Загрузка…</div>;
  return (
    <div className="p-4 space-y-4">
      <div className="grid grid-cols-5 gap-2">
        <Card label="OOS Sharpe" value="1.84" tone="pos" />
        <Card label="CAGR" value="+18.2%" tone="pos" />
        <Card label="Win Rate" value="54.7%" />
        <Card label="Profit Factor" value="1.62" />
        <Card label="Max DD" value="-7.2%" tone="neg" />
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-text-dim font-semibold mb-2.5">
          Walk-Forward Folds
        </div>
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-surface">
              {['Fold', 'Train', 'Test', 'Sharpe', 'CAGR', 'Win%', 'Trades', 'Max DD'].map((h) => (
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
            {data.map((f) => (
              <tr key={f.id} className="hover:bg-surface-2">
                <td className="px-3 py-2 mono border-b border-border-soft">{f.id}</td>
                <td className="px-3 py-2 mono border-b border-border-soft">
                  {f.trainStart.slice(0, 7)} → {f.trainEnd.slice(0, 7)}
                </td>
                <td className="px-3 py-2 mono border-b border-border-soft">
                  {f.testStart.slice(0, 7)} → {f.testEnd.slice(0, 7)}
                </td>
                <td className="px-3 py-2 mono border-b border-border-soft text-green">{f.sharpe.toFixed(2)}</td>
                <td className="px-3 py-2 mono border-b border-border-soft text-green">{formatPct(f.cagr * 100)}</td>
                <td className="px-3 py-2 mono border-b border-border-soft">
                  {(f.winRate * 100).toFixed(1)}%
                </td>
                <td className="px-3 py-2 mono border-b border-border-soft">{f.trades}</td>
                <td className="px-3 py-2 mono border-b border-border-soft text-red">
                  {formatPct(f.maxDd * 100)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-text-dim font-semibold mb-2.5">
          Equity vs IMOEX
        </div>
        <div className="h-[220px] bg-surface border border-border rounded-md p-3">
          <EquityCurve
            data={[
              1111942, 1112542, 1113142, 1113642, 1114742, 1115742, 1116742, 1117642, 1118742, 1119642,
              1120342, 1120842, 1121542, 1122142, 1122942, 1123642, 1124542, 1125342, 1126342, 1127142,
              1127742, 1128242, 1128942, 1129642, 1130342, 1130842, 1131442, 1132242, 1133142, 1124380,
            ]}
            color="#5d5fef"
          />
        </div>
      </div>
    </div>
  );
}

function Card({ label, value, tone }: { label: string; value: string; tone?: 'pos' | 'neg' }) {
  return (
    <div className="bg-surface-2 border border-border rounded-md px-3 py-2.5">
      <div className="text-[10px] text-text-dim uppercase tracking-wider">{label}</div>
      <div
        className={`mono text-[18px] mt-1 ${tone === 'pos' ? 'text-green' : tone === 'neg' ? 'text-red' : 'text-text'}`}
      >
        {value}
      </div>
    </div>
  );
}
