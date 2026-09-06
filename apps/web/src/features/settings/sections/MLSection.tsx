import { useMemo } from 'react';
import type { MLSettings, RegimeFilter } from '@algotrader/shared';
import { Section } from '../Section';
import { NumberField, SelectField, TextField } from '../Field';

interface Props {
  values: MLSettings;
  onChange: (patch: Partial<MLSettings>) => void;
  onSave: () => void;
  saving: boolean;
  dirty: boolean;
  disabled?: boolean;
}

export function MLSection({ values, onChange, onSave, saving, dirty, disabled }: Props) {
  const retrainError = useMemo(() => {
    if (values.retrainIntervalDays < 1) return 'Минимум 1 день';
    if (values.retrainIntervalDays > 90) return 'Максимум 90 дней';
    return null;
  }, [values.retrainIntervalDays]);

  const confError = useMemo(() => {
    if (values.confidenceThreshold < 0) return 'Минимум 0';
    if (values.confidenceThreshold > 1) return 'Максимум 1';
    return null;
  }, [values.confidenceThreshold]);

  const hasErrors = retrainError || confError;

  return (
    <Section
      title="ML"
      description="Параметры модели и сигнального фильтра."
      onSave={onSave}
      saving={saving}
      dirty={dirty && !hasErrors}
      disabled={disabled}
    >
      <TextField
        label="Версия модели"
        value={values.modelVersion}
        onChange={() => {
          /* readonly */
        }}
        readOnly
        hint="Обновляется после retrain. Менять вручную нельзя."
        disabled={disabled}
      />
      <NumberField
        label="Интервал retrain (дни)"
        value={values.retrainIntervalDays}
        min={1}
        max={90}
        step={1}
        onChange={(v) => onChange({ retrainIntervalDays: Math.round(v) })}
        error={retrainError}
        disabled={disabled}
      />
      <NumberField
        label="Confidence threshold"
        value={values.confidenceThreshold}
        min={0}
        max={1}
        step={0.05}
        onChange={(v) => onChange({ confidenceThreshold: v })}
        error={confError}
        hint="Сигналы ниже этого порога игнорируются"
        disabled={disabled}
      />
      <SelectField
        label="Regime filter"
        value={values.regimeFilter}
        onChange={(v) => onChange({ regimeFilter: v as RegimeFilter })}
        options={[
          { value: 'all', label: 'Все режимы' },
          { value: 'trend', label: 'Только тренд' },
          { value: 'range', label: 'Только рейндж' },
          { value: 'volatile', label: 'Только волатильный' },
        ]}
        disabled={disabled}
      />
    </Section>
  );
}
