import { useId, useState, type ChangeEvent, type ReactNode } from 'react';

interface FieldBaseProps {
  label: string;
  hint?: string;
  error?: string | null;
  disabled?: boolean;
  status?: { label: string; tone?: 'ok' | 'warn' | 'err' | 'dim' };
}

interface TextFieldProps extends FieldBaseProps {
  type?: 'text' | 'password';
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  readOnly?: boolean;
  /** When true, render a keyboard-accessible reveal toggle (eye) on
   *  password inputs and an optional clear (×) button. */
  reveal?: boolean;
  /** Required when reveal is true: clicked to wipe the field. */
  onClear?: () => void;
}

export function TextField({
  label,
  hint,
  error,
  disabled,
  status,
  type = 'text',
  value,
  onChange,
  placeholder,
  readOnly = false,
  reveal = false,
  onClear,
}: TextFieldProps) {
  const id = useId();
  const [shown, setShown] = useState(false);
  const effectiveType = reveal && type === 'password' ? (shown ? 'text' : 'password') : type;
  return (
    <FieldShell label={label} hint={hint} error={error} disabled={disabled} status={status} htmlFor={id}>
      <div className={reveal ? 'relative' : undefined}>
        <input
          id={id}
          type={effectiveType}
          value={value}
          onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          className={`w-full bg-bg border border-border rounded px-2 py-1.5 text-sm mono focus:outline-none focus:border-accent disabled:opacity-50 ${
            reveal ? 'pr-20' : ''
          }`}
          readOnly={readOnly}
        />
        {reveal && (
          <div className="absolute right-1 top-1/2 -translate-y-1/2 flex gap-1">
            <button
              type="button"
              onClick={() => setShown((s) => !s)}
              disabled={disabled}
              aria-pressed={shown}
              aria-label={shown ? 'Скрыть токен' : 'Показать токен'}
              className="px-1.5 py-0.5 text-[10px] rounded border border-border text-text-muted hover:text-accent hover:border-accent disabled:opacity-50"
            >
              {shown ? 'Скрыть' : 'Показать'}
            </button>
            {onClear && (
              <button
                type="button"
                onClick={onClear}
                disabled={disabled || !value}
                aria-label="Очистить"
                className="px-1.5 py-0.5 text-[10px] rounded border border-border text-text-muted hover:text-accent hover:border-accent disabled:opacity-50"
              >
                ×
              </button>
            )}
          </div>
        )}
      </div>
    </FieldShell>
  );
}

interface NumberFieldProps extends FieldBaseProps {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (v: number) => void;
}

export function NumberField({ label, hint, error, disabled, status, value, min, max, step, onChange }: NumberFieldProps) {
  const id = useId();
  return (
    <FieldShell label={label} hint={hint} error={error} disabled={disabled} status={status} htmlFor={id}>
      <input
        id={id}
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e: ChangeEvent<HTMLInputElement>) => {
          const n = parseFloat(e.target.value);
          onChange(Number.isNaN(n) ? 0 : n);
        }}
        disabled={disabled}
        className="w-full bg-bg border border-border rounded px-2 py-1.5 text-sm mono focus:outline-none focus:border-accent"
      />
    </FieldShell>
  );
}

interface SelectFieldProps extends FieldBaseProps {
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (v: string) => void;
}

export function SelectField({ label, hint, error, disabled, status, value, options, onChange }: SelectFieldProps) {
  const id = useId();
  return (
    <FieldShell label={label} hint={hint} error={error} disabled={disabled} status={status} htmlFor={id}>
      <select
        id={id}
        value={value}
        onChange={(e: ChangeEvent<HTMLSelectElement>) => onChange(e.target.value)}
        disabled={disabled}
        className="w-full bg-bg border border-border rounded px-2 py-1.5 text-sm focus:outline-none focus:border-accent"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </FieldShell>
  );
}

interface CheckboxFieldProps extends FieldBaseProps {
  checked: boolean;
  onChange: (v: boolean) => void;
}

export function CheckboxField({ label, hint, error, disabled, status, checked, onChange }: CheckboxFieldProps) {
  const id = useId();
  return (
    <FieldShell label={label} hint={hint} error={error} disabled={disabled} status={status} htmlFor={id} inline>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.checked)}
        disabled={disabled}
        className="w-4 h-4 accent-accent"
      />
    </FieldShell>
  );
}

function FieldShell({
  label,
  hint,
  error,
  disabled,
  status,
  htmlFor,
  inline = false,
  children,
}: FieldBaseProps & { htmlFor: string; inline?: boolean; children: ReactNode }) {
  return (
    <div className={`flex ${inline ? 'flex-row items-center gap-2' : 'flex-col gap-1'} ${disabled ? 'opacity-50' : ''}`}>
      <div className={`flex items-center gap-2 ${inline ? 'flex-1' : ''}`}>
        <label htmlFor={htmlFor} className="text-[10px] uppercase text-text-dim tracking-wider">
          {label}
        </label>
        {status && <FieldStatus tone={status.tone ?? 'dim'}>{status.label}</FieldStatus>}
      </div>
      <div className={`${inline ? 'flex-1' : 'w-full'} ${disabled ? 'pointer-events-none' : ''}`}>{children}</div>
      {hint && !error && <span className="text-[10px] text-text-muted">{hint}</span>}
      {error && (
        <span className="text-[10px] text-red flex items-center gap-1" role="alert">
          <span aria-hidden="true">●</span>
          {error}
        </span>
      )}
    </div>
  );
}

interface FieldStatusProps {
  tone: 'ok' | 'warn' | 'err' | 'dim';
  children: ReactNode;
}

/** Decorative status pill rendered next to a field label. Decorative
 *  because the field's hint carries the announced meaning; the pill is
 *  for at-a-glance scan. */
export function FieldStatus({ tone, children }: FieldStatusProps) {
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
