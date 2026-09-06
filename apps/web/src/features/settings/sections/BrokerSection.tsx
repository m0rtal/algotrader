import { useState } from 'react';
import { maskToken, type BrokerSettings } from '@algotrader/shared';
import { ConfirmDialog } from '../ConfirmDialog';
import { Section } from '../Section';
import { SelectField, TextField } from '../Field';

interface Props {
  values: BrokerSettings;
  onChange: (patch: Partial<BrokerSettings>) => void;
  onSave: () => void;
  saving: boolean;
  dirty: boolean;
  disabled?: boolean;
}

export function BrokerSection({ values, onChange, onSave, saving, dirty, disabled }: Props) {
  const [showLiveConfirm, setShowLiveConfirm] = useState(false);

  const handleEnvChange = (next: BrokerSettings['environment']) => {
    if (next === 'live' && values.environment !== 'live') {
      setShowLiveConfirm(true);
      return;
    }
    onChange({ environment: next });
  };

  const confirmLive = () => {
    onChange({ environment: 'live' });
    setShowLiveConfirm(false);
  };

  return (
    <>
      <Section
        title="Брокер"
        description="Подключение к Tinkoff Investments API. Sandbox безопасен, Live — реальные деньги."
        onSave={onSave}
        saving={saving}
        dirty={dirty}
        disabled={disabled}
      >
        <SelectField
          label="Окружение"
          value={values.environment}
          onChange={(v) => handleEnvChange(v as BrokerSettings['environment'])}
          options={[
            { value: 'sandbox', label: 'Sandbox (безопасно)' },
            { value: 'live', label: 'Live (реальные деньги)' },
          ]}
          disabled={disabled}
        />
        <TextField
          label="Токен"
          type="password"
          value={maskToken(values.tokenLast4)}
          onChange={() => {
            // Read-only display — actual input is below
          }}
          placeholder="••••••ABCD"
          hint="Показано последние 4 символа. Полный токен хранится на бэкенде."
          readOnly
          disabled={disabled}
        />
        <TextField
          label="Account ID"
          value={values.accountId}
          onChange={(v) => onChange({ accountId: v })}
          placeholder="ACC-XXXXXXX"
          disabled={disabled}
        />
        <div className="hidden" aria-hidden="true">
          {/* Hidden mirror of token for PUT redacted flag; not user-visible */}
          <input type="text" tabIndex={-1} aria-hidden value="redacted" readOnly />
        </div>
      </Section>
      <ConfirmDialog
        open={showLiveConfirm}
        title="Включить live-торговлю?"
        body="Реальные деньги. Убедитесь что risk-лимиты настроены корректно."
        confirmLabel="Включить Live"
        cancelLabel="Отмена"
        danger
        onConfirm={confirmLive}
        onCancel={() => setShowLiveConfirm(false)}
      />
    </>
  );
}
