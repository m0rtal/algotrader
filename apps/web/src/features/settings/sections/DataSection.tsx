import { useMemo } from 'react';
import type { DataSettings, DataSource } from '@algotrader/shared';
import { Section } from '../Section';
import { CheckboxField, NumberField, SelectField } from '../Field';

interface Props {
  values: DataSettings;
  onChange: (patch: Partial<DataSettings>) => void;
  onSave: () => void;
  saving: boolean;
  dirty: boolean;
  disabled?: boolean;
}

export function DataSection({ values, onChange, onSave, saving, dirty, disabled }: Props) {
  const cacheError = useMemo(() => {
    if (values.cacheTtlMinutes < 1) return 'Минимум 1 минута';
    if (values.cacheTtlMinutes > 1440) return 'Максимум 1440 минут (24ч)';
    return null;
  }, [values.cacheTtlMinutes]);

  const historyError = useMemo(() => {
    if (values.historyYears < 1) return 'Минимум 1 год';
    if (values.historyYears > 10) return 'Максимум 10 лет';
    return null;
  }, [values.historyYears]);

  const hasErrors = cacheError || historyError;

  return (
    <Section
      title="Данные"
      description="Источник рыночных данных, кэш, период истории."
      onSave={onSave}
      saving={saving}
      dirty={dirty && !hasErrors}
      disabled={disabled}
    >
      <SelectField
        label="Источник"
        value={values.source}
        onChange={(v) => onChange({ source: v as DataSource })}
        options={[
          { value: 'tinkoff', label: 'Tinkoff API' },
          { value: 'moex_iss', label: 'MOEX ISS (бесплатно)' },
          { value: 'file', label: 'Локальные parquet файлы' },
        ]}
        disabled={disabled}
      />
      <NumberField
        label="Cache TTL (мин)"
        value={values.cacheTtlMinutes}
        min={1}
        max={1440}
        step={5}
        onChange={(v) => onChange({ cacheTtlMinutes: Math.round(v) })}
        error={cacheError}
        disabled={disabled}
      />
      <NumberField
        label="История (лет)"
        value={values.historyYears}
        min={1}
        max={10}
        step={1}
        onChange={(v) => onChange({ historyYears: Math.round(v) })}
        error={historyError}
        hint="Применяется при следующем fetch.py"
        disabled={disabled}
      />
      <CheckboxField
        label="Auto-fetch"
        checked={values.autoFetch}
        onChange={(v) => onChange({ autoFetch: v })}
        hint="Cron: fetch.py ежедневно в 19:30 МСК"
        disabled={disabled}
      />
    </Section>
  );
}
