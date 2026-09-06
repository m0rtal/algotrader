import { useMemo, useState } from 'react';
import type { RiskSettings } from '@algotrader/shared';
import { ConfirmDialog } from '../ConfirmDialog';
import { Section } from '../Section';
import { CheckboxField, NumberField } from '../Field';

interface Props {
  values: RiskSettings;
  onChange: (patch: Partial<RiskSettings>) => void;
  onSave: () => void;
  saving: boolean;
  dirty: boolean;
  disabled?: boolean;
}

const DD_RANGE = { min: 1, max: 50 } as const;
const POS_RANGE = { min: 1, max: 100 } as const;

export function RiskSection({ values, onChange, onSave, saving, dirty, disabled }: Props) {
  const [showKillConfirm, setShowKillConfirm] = useState(false);

  const ddError = useMemo(() => {
    if (values.maxDrawdownPct < DD_RANGE.min) return `Минимум ${DD_RANGE.min}%`;
    if (values.maxDrawdownPct > DD_RANGE.max) return `Максимум ${DD_RANGE.max}%`;
    return null;
  }, [values.maxDrawdownPct]);

  const posError = useMemo(() => {
    if (values.maxPositionSizePct < POS_RANGE.min) return `Минимум ${POS_RANGE.min}%`;
    if (values.maxPositionSizePct > POS_RANGE.max) return `Максимум ${POS_RANGE.max}%`;
    return null;
  }, [values.maxPositionSizePct]);

  const thresholdError = useMemo(() => {
    if (!values.killSwitchEnabled) return null;
    if (values.killSwitchThresholdPct < DD_RANGE.min) return `Минимум ${DD_RANGE.min}%`;
    if (values.killSwitchThresholdPct > DD_RANGE.max) return `Максимум ${DD_RANGE.max}%`;
    if (values.killSwitchThresholdPct < values.maxDrawdownPct) {
      return 'Kill switch порог должен быть больше max drawdown';
    }
    return null;
  }, [values.killSwitchThresholdPct, values.maxDrawdownPct]);

  const hasErrors = ddError || posError || thresholdError;

  const handleKillToggle = (next: boolean) => {
    if (!next && values.killSwitchEnabled) {
      setShowKillConfirm(true);
      return;
    }
    onChange({ killSwitchEnabled: next });
  };

  const confirmDisable = () => {
    onChange({ killSwitchEnabled: false });
    setShowKillConfirm(false);
  };

  const handleThresholdChange = (v: number) => {
    // ponytail: threshold validation lives in SettingsSchema.refine; UI still calls onChange
    onChange({ killSwitchThresholdPct: v });
  };

  return (
    <>
      <Section
        title="Риск"
        description="Лимиты убытков и аварийное отключение."
        onSave={onSave}
        saving={saving}
        dirty={dirty && !hasErrors}
        disabled={disabled}
      >
        <NumberField
          label="Макс drawdown (%)"
          value={values.maxDrawdownPct}
          min={DD_RANGE.min}
          max={DD_RANGE.max}
          step={1}
          onChange={(v) => onChange({ maxDrawdownPct: Math.round(v) })}
          error={ddError}
          hint="При превышении — пауза торговли."
          disabled={disabled}
        />
        <NumberField
          label="Макс размер позиции (%)"
          value={values.maxPositionSizePct}
          min={POS_RANGE.min}
          max={POS_RANGE.max}
          step={1}
          onChange={(v) => onChange({ maxPositionSizePct: Math.round(v) })}
          error={posError}
          hint="Доля портфеля в одной позиции."
          disabled={disabled}
        />
        <CheckboxField
          label="Kill switch"
          checked={values.killSwitchEnabled}
          onChange={handleKillToggle}
          hint="Автоотключение при пороге ниже."
          disabled={disabled}
        />
        <NumberField
          label="Kill switch порог (%)"
          value={values.killSwitchThresholdPct}
          min={DD_RANGE.min}
          max={DD_RANGE.max}
          step={1}
          onChange={handleThresholdChange}
          error={thresholdError}
          hint="Срабатывает раньше max drawdown."
          disabled={disabled || !values.killSwitchEnabled}
        />
      </Section>
      <ConfirmDialog
        open={showKillConfirm}
        title="Отключить kill switch?"
        body="Без kill switch убытки могут превысить заданный порог."
        confirmLabel="Отключить"
        danger
        onConfirm={confirmDisable}
        onCancel={() => setShowKillConfirm(false)}
      />
    </>
  );
}
