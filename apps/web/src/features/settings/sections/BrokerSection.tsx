import { useState } from 'react';
import { type BrokerSettings } from '@algotrader/shared';
import { Section } from '../Section';
import { TextField } from '../Field';

interface Props {
  values: BrokerSettings;
  onChange: (patch: Partial<BrokerSettings>) => void;
  onSave: () => void;
  saving: boolean;
  dirty: boolean;
  disabled?: boolean;
  /** Token draft lives in the parent so save fan-out can read it. */
  tokenDraft: string;
  onTokenDraftChange: (v: string) => void;
}

// Token format: starts with "t.", then [A-Za-z0-9._-], 20–512 chars total.
const TOKEN_PREFIX = 't.';
const TOKEN_MIN = 20;
const TOKEN_MAX = 512;
const TOKEN_RE = /^t\.[A-Za-z0-9._-]+$/;

function validateToken(draft: string): string | null {
  const t = draft.trim();
  if (!t) return null;
  if (t.length < TOKEN_MIN) return `Минимум ${TOKEN_MIN} символов (сейчас ${t.length}).`;
  if (t.length > TOKEN_MAX) return `Максимум ${TOKEN_MAX} символов (сейчас ${t.length}).`;
  if (!t.startsWith(TOKEN_PREFIX)) return `Токен Tinkoff начинается с «${TOKEN_PREFIX}».`;
  if (!TOKEN_RE.test(t)) return 'Допустимы только A–Z, a–z, 0–9, точка, подчёркивание, дефис.';
  return null;
}

export function BrokerSection({
  values,
  onChange,
  onSave,
  saving,
  dirty,
  disabled,
  tokenDraft,
  onTokenDraftChange,
}: Props) {
  const [touched, setTouched] = useState(false);

  const tokenError = touched ? validateToken(tokenDraft) : null;
  const tokenHasDraft = tokenDraft.trim().length > 0;
  const tokenOk = tokenHasDraft && !tokenError;

  const envIsLive = values.environment === 'live';

  const handleEnvChange = (next: BrokerSettings['environment']) => {
    // No confirm dialog — inline warning below the radio cards carries
    // the risk. Selection is acknowledged by leaving Live chosen and
    // clicking Save.
    onChange({ environment: next });
  };

  const envStatus = envIsLive
    ? { label: 'live', tone: 'warn' as const }
    : { label: 'sandbox', tone: 'ok' as const };

  const tokenStatus = !tokenHasDraft
    ? values.tokenLast4
      ? { label: 'задан', tone: 'ok' as const }
      : { label: 'не задан', tone: 'warn' as const }
    : tokenOk
      ? { label: 'готов', tone: 'ok' as const }
      : { label: 'проверьте', tone: 'err' as const };

  const accountStatus = values.accountId
    ? { label: 'заполнен', tone: 'ok' as const }
    : { label: 'пусто', tone: 'warn' as const };

  return (
    <Section
      title="Брокер"
      description="Подключение к Tinkoff Investments API. Sandbox безопасен, Live — реальные деньги."
      onSave={onSave}
      saving={saving}
      dirty={dirty || tokenHasDraft}
      disabled={disabled}
    >
      <div className="md:col-span-2 flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase text-text-dim tracking-wider">Окружение</span>
          <FieldStatusLite tone={envStatus.tone}>{envStatus.label}</FieldStatusLite>
        </div>
        <div role="radiogroup" aria-label="Окружение" className="grid grid-cols-2 gap-2">
          <EnvRadioCard
            label="Sandbox"
            sub="Тестовые счета. Без реальных денег."
            selected={values.environment === 'sandbox'}
            onSelect={() => handleEnvChange('sandbox')}
            tone="ok"
          />
          <EnvRadioCard
            label="Live"
            sub="Реальные деньги. Проверьте risk-лимиты."
            selected={values.environment === 'live'}
            onSelect={() => handleEnvChange('live')}
            tone="warn"
          />
        </div>
        {envIsLive && (
          <aside
            role="note"
            aria-label="Предупреждение о реальных деньгах"
            className="mt-1 border border-amber rounded-md p-2 bg-amber/10 text-amber text-xs flex gap-2"
          >
            <span aria-hidden="true">⚠</span>
            <div>
              <strong className="block mb-0.5">Live — реальные деньги.</strong>
              Проверьте risk-лимиты во вкладке «Риск» перед включением. Любая
              активная стратегия начнёт торговать сразу после сохранения.
            </div>
          </aside>
        )}
      </div>

      <div className="md:col-span-2 flex flex-col gap-1.5">
        <TextField
          label="Токен"
          type="password"
          value={tokenDraft}
          onChange={(v) => {
            onTokenDraftChange(v);
            setTouched(true);
            onChange({ tokenRedacted: true });
          }}
          onClear={tokenHasDraft ? () => onTokenDraftChange('') : undefined}
          placeholder="Вставьте токен Tinkoff"
          hint={
            tokenError
              ? undefined
              : tokenHasDraft
                ? 'Готов к записи. Нажмите «Сохранить».'
                : values.tokenLast4
                  ? `Текущий: t.••••••••${values.tokenLast4}. Вставьте новый чтобы заменить.`
                  : 'Токен не задан. Вставьте токен Tinkoff и сохраните.'
          }
          error={tokenError ?? undefined}
          disabled={disabled}
          reveal
          status={tokenStatus}
        />
      </div>

      <div className="md:col-span-2">
        <TextField
          label="Account ID"
          value={values.accountId}
          onChange={(v) => onChange({ accountId: v })}
          placeholder="ACC-XXXXXXX"
          hint={values.accountId ? undefined : 'Укажите аккаунт Tinkoff, к которому подключён токен.'}
          disabled={disabled}
          status={accountStatus}
        />
      </div>
    </Section>
  );
}

function EnvRadioCard({
  label,
  sub,
  selected,
  onSelect,
  tone,
}: {
  label: string;
  sub: string;
  selected: boolean;
  onSelect: () => void;
  tone: 'ok' | 'warn';
}) {
  const ring =
    tone === 'warn' && selected
      ? 'border-amber bg-amber/10'
      : selected
        ? 'border-accent bg-accent/10'
        : 'border-border hover:border-text-muted';
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={`text-left border rounded-md p-2.5 transition-colors ${ring}`}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">{label}</span>
        {selected && (
          <span className={`text-[10px] uppercase tracking-wider ${tone === 'warn' ? 'text-amber' : 'text-green'}`}>
            ● выбрано
          </span>
        )}
      </div>
      <div className="text-[11px] text-text-muted mt-1">{sub}</div>
    </button>
  );
}

// Local copy of the status pill — kept inline so this component does
// not need a separate import surface for a tiny visual atom. Logic is
// identical to <FieldStatus/> in Field.tsx; the import path is used by
// TextField for its `status` prop while this one is used in the env
// row where the label is rendered outside FieldShell.
function FieldStatusLite({
  tone,
  children,
}: {
  tone: 'ok' | 'warn' | 'err' | 'dim';
  children: React.ReactNode;
}) {
  const cls =
    tone === 'ok'
      ? 'bg-green-soft text-green'
      : tone === 'warn'
        ? 'bg-amber/15 text-amber'
        : tone === 'err'
          ? 'bg-red-soft text-red'
          : 'bg-surface-2 text-text-dim';
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wider ${cls}`}
      aria-hidden="true"
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {children}
    </span>
  );
}

// Re-export kept for callers that already imported useSaveToken from this
// module. The actual save now flows through the parent section's onSave
// handler — the fan-out (POST /api/settings/token then PUT /api/settings)
// is orchestrated by SettingsTab, not here.
export { useSaveToken } from '@lib/hooks';
