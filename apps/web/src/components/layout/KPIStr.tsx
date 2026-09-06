import { useKpis } from '@lib/hooks';

export function KPIStr() {
  const { data: kpis } = useKpis();
  if (!kpis) return null;
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 bg-surface">
      {kpis.map((k) => (
        <div key={k.label} className="px-[18px] py-3 border-r border-border-soft last:border-r-0">
          <div className="text-[10px] uppercase text-text-dim tracking-wider mb-1">{k.label}</div>
          <div
            className={`mono text-[20px] font-medium ${
              k.tone === 'pos' ? 'text-green' : k.tone === 'neg' ? 'text-red' : 'text-text'
            }`}
          >
            {k.value}
          </div>
          {k.sub && (
            <div
              className={`mono text-[11px] mt-0.5 ${
                k.sub.startsWith('+') ? 'text-green' : k.sub.startsWith('-') ? 'text-red' : 'text-text-muted'
              }`}
            >
              {k.sub}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
