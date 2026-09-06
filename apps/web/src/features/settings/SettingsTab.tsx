import { useEffect, useState } from 'react';
import { DEFAULT_SETTINGS } from '@algotrader/shared';
import { useDeleteSettings, useSaveSettings, useSettings } from '@lib/hooks';
import { useSettingsStore } from '@stores/settingsStore';
import { BrokerSection } from '@features/settings/sections/BrokerSection';
import { DataSection } from '@features/settings/sections/DataSection';
import { DangerZone } from '@features/settings/DangerZone';
import { MLSection } from '@features/settings/sections/MLSection';
import { RiskSection } from '@features/settings/sections/RiskSection';
import { Toast } from '@features/settings/Toast';

type SectionId = 'broker' | 'risk' | 'ml' | 'data';

const TABS: ReadonlyArray<{ id: SectionId; label: string }> = [
  { id: 'broker', label: 'Брокер' },
  { id: 'risk', label: 'Риск' },
  { id: 'ml', label: 'ML' },
  { id: 'data', label: 'Данные' },
];

export function SettingsTab() {
  const { data, isLoading, isError, error } = useSettings();
  const saveMutation = useSaveSettings();
  const deleteMutation = useDeleteSettings();
  const { values, loaded, update, setValues, isDirty, version } = useSettingsStore();
  const [active, setActive] = useState<SectionId>('broker');
  const [toast, setToast] = useState<{ message: string; tone: 'ok' | 'warn' | 'err' | 'info' } | null>(null);

  useEffect(() => {
    if (data) setValues(data.values, data.version);
  }, [data, setValues]);

  const showToast = (message: string, tone: 'ok' | 'warn' | 'err' | 'info') => setToast({ message, tone });

  const handleSave = async () => {
    try {
      const res = await saveMutation.mutateAsync({ values, version: version ?? 'v1' });
      setValues(res.values, res.version);
      showToast('Сохранено', 'ok');
    } catch (e) {
      const err = e as Error & { status?: number };
      if (err.status === 409) {
        showToast('Настройки изменены в другом месте. Перезагрузите.', 'warn');
      } else {
        showToast('Ошибка сохранения. Попробуйте ещё раз.', 'err');
      }
    }
  };

  const handleReset = async () => {
    try {
      await deleteMutation.mutateAsync();
      setValues(DEFAULT_SETTINGS, null);
      showToast('Настройки сброшены', 'ok');
    } catch {
      showToast('Ошибка сброса', 'err');
    }
  };

  const updateActive = (patch: unknown) => update(active, patch as never);

  if (isLoading) return <div className="p-4 text-text-muted">Загрузка настроек…</div>;
  if (isError) {
    return <div className="p-4 text-red">Ошибка: {(error as Error)?.message ?? 'unknown'}</div>;
  }

  return (
    <div className="p-4">
      <div className="flex gap-1 mb-3 border-b border-border">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setActive(t.id)}
            className={`px-3 py-1.5 text-xs border-b-2 -mb-px ${
              active === t.id ? 'border-accent text-accent' : 'border-transparent text-text-muted hover:text-text'
            }`}
            data-testid={`section-tab-${t.id}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {active === 'broker' && (
        <BrokerSection values={values.broker} onChange={updateActive} onSave={handleSave} saving={saveMutation.isPending} dirty={loaded && isDirty()} />
      )}
      {active === 'risk' && (
        <RiskSection values={values.risk} onChange={updateActive} onSave={handleSave} saving={saveMutation.isPending} dirty={loaded && isDirty()} />
      )}
      {active === 'ml' && (
        <MLSection values={values.ml} onChange={updateActive} onSave={handleSave} saving={saveMutation.isPending} dirty={loaded && isDirty()} />
      )}
      {active === 'data' && (
        <DataSection values={values.data} onChange={updateActive} onSave={handleSave} saving={saveMutation.isPending} dirty={loaded && isDirty()} />
      )}
      <DangerZone onReset={handleReset} busy={deleteMutation.isPending} />
      {toast && (
        <div className="fixed bottom-4 right-4 z-50">
          <Toast message={toast.message} tone={toast.tone} onDismiss={() => setToast(null)} />
        </div>
      )}
    </div>
  );
}
