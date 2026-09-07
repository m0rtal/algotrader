import { useState } from 'react';
import { type BrokerSettings } from '@algotrader/shared';
import { useSaveToken } from '@lib/hooks';
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
  // Local token draft. Empty string sent to backend means "keep existing".
  const [tokenDraft, setTokenDraft] = useState('');
  const [tokenTouched, setTokenTouched] = useState(false);
  const [tokenSaveStatus, setTokenSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>(
    'idle',
  );
  const [tokenSaveError, setTokenSaveError] = useState<string | null>(null);
  const saveToken = useSaveToken();

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

  const handleSaveToken = async () => {
    // ponytail: defensive — button is also disabled when draft is whitespace,
    // so this guard handles the "force-click" case only.
    /* c8 ignore next */
    if (!tokenDraft.trim()) return;
    setTokenSaveStatus('saving');
    setTokenSaveError(null);
    try {
      const res = await saveToken.mutateAsync({ token: tokenDraft.trim() });
      // Reflect new last-4 immediately; clear draft.
      onChange({ tokenLast4: res.tokenLast4, tokenRedacted: true });
      setTokenDraft('');
      setTokenTouched(false);
      setTokenSaveStatus('saved');
      setTimeout(() => setTokenSaveStatus('idle'), 3000);
    } catch (e) {
      const err = e as Error & { status?: number };
      setTokenSaveStatus('error');
      // ponytail: defensive fallback — Error.message is usually present but
      // custom error classes may omit it.
      /* c8 ignore next */
      setTokenSaveError(err.message ?? 'save failed');
      setTimeout(() => setTokenSaveStatus('idle'), 5000);
    }
  };

  const tokenHint =
    tokenSaveStatus === 'saving'
      ? 'Запись токена…'
      : tokenSaveStatus === 'saved'
        ? `Токен сохранён (последние 4: ${values.tokenLast4 || '—'}). Воркер подхватит при следующем запуске.`
        : tokenSaveStatus === 'error'
          ? // ponytail: defensive — tokenSaveError is always set when status='error',
            // but TS doesn't know that, so the fallback is logically unreachable.
            /* c8 ignore next */
            `Ошибка: ${tokenSaveError ?? 'неизвестно'}`
          : tokenTouched
            ? 'Нажмите «Сохранить токен» чтобы записать на бэкенд. Пустой ввод оставляет существующий.'
            : values.tokenLast4
              ? `Текущий: ••••••••${values.tokenLast4}. Полный токен хранится на бэкенде. Вставьте новый чтобы заменить.`
              : 'Токен не задан. Вставьте токен Tinkoff и нажмите «Сохранить токен».';

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
        <div className="space-y-2">
          <TextField
            label="Токен"
            type="password"
            value={tokenDraft}
            onChange={(v) => {
              setTokenDraft(v);
              setTokenTouched(true);
              setTokenSaveStatus('idle');
              // Mark token as redacted for the local broker view; actual write
              // happens on Save Token, not on Save Settings.
              onChange({ tokenRedacted: true });
            }}
            placeholder="Вставьте токен Tinkoff"
            hint={tokenHint}
            disabled={disabled || tokenSaveStatus === 'saving'}
          />
          <button
            type="button"
            onClick={handleSaveToken}
            disabled={disabled || !tokenDraft.trim() || tokenSaveStatus === 'saving'}
            data-testid="save-token"
            className="px-3 py-1.5 text-xs rounded border border-accent text-accent hover:bg-accent/10 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {tokenSaveStatus === 'saving' ? 'Сохранение…' : 'Сохранить токен'}
          </button>
        </div>
        <TextField
          label="Account ID"
          value={values.accountId}
          onChange={(v) => onChange({ accountId: v })}
          placeholder="ACC-XXXXXXX"
          disabled={disabled}
        />
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
