import { useModel, useModelFeatures, usePipeline } from '@lib/hooks';

export function RightRail() {
  const { data: model } = useModel();
  const { data: features } = useModelFeatures();
  const { data: pipeline } = usePipeline();
  if (!model || !features || !pipeline) return null;
  return (
    <div className="p-4 space-y-3">
      <section className="bg-surface-2 border border-border rounded-md p-3">
        <h3 className="text-[10px] uppercase tracking-wider text-text-dim font-semibold mb-2">ML Model</h3>
        <Row label="Версия" value={model.version} />
        <Row label="Train окно" value={`${model.trainWindowMonths} мес`} />
        <Row label="Train period" value={`${model.trainStart.slice(0, 7)} → ${model.trainEnd.slice(0, 7)}`} />
        <Row label="OOS accuracy" value={model.oosAccuracy.toFixed(3)} tone="pos" />
        <Row label="OOS Sharpe" value={model.oosSharpe.toFixed(2)} />
        <Row label="IC (rank)" value={model.ic.toFixed(3)} tone="pos" />
        <Row label="Last train" value={model.lastTrainDate} />
        <Row label="Next retrain" value={model.nextTrainDate} />
      </section>
      <section className="bg-surface-2 border border-border rounded-md p-3">
        <h3 className="text-[10px] uppercase tracking-wider text-text-dim font-semibold mb-2">Top features</h3>
        {features.map((f) => (
          <div key={f.name} className="flex items-center gap-2 py-0.5 text-[11px]">
            <span className="w-[70px] text-text-muted mono">{f.name}</span>
            <span className="w-[60px] h-1 bg-border-soft rounded-sm overflow-hidden">
              <span
                className="block h-full"
                style={{ width: `${f.importance * 1000}%`, background: 'var(--color-accent)' }}
              />
            </span>
            <span className="mono text-text w-[36px] text-right">{f.importance.toFixed(3)}</span>
          </div>
        ))}
      </section>
      <section className="bg-surface-2 border border-border rounded-md p-3">
        <h3 className="text-[10px] uppercase tracking-wider text-text-dim font-semibold mb-2">
          Pipeline status
        </h3>
        {pipeline.map((s) => (
          <Row key={s.name} label={s.name} value={s.status === 'ok' ? `✓ ${s.detail ?? ''}` : s.status === 'idle' ? `○ ${s.detail ?? ''}` : s.status} tone={s.status === 'ok' ? 'pos' : s.status === 'err' ? 'neg' : undefined} />
        ))}
      </section>
    </div>
  );
}

function Row({ label, value, tone }: { label: string; value: string; tone?: 'pos' | 'neg' }) {
  return (
    <div className="flex justify-between items-center py-0.5 text-xs gap-2">
      <span className="text-text-muted">{label}</span>
      <span
        className={`mono ${tone === 'pos' ? 'text-green' : tone === 'neg' ? 'text-red' : 'text-text'}`}
      >
        {value}
      </span>
    </div>
  );
}
