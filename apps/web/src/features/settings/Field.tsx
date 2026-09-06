import { useId, type ChangeEvent, type ReactNode } from 'react';

interface FieldBaseProps {
  label: string;
  hint?: string;
  error?: string | null;
  disabled?: boolean;
}

interface TextFieldProps extends FieldBaseProps {
  type?: 'text' | 'password';
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}

export function TextField({ label, hint, error, disabled, type = 'text', value, onChange, placeholder, readOnly = false }: TextFieldProps & { readOnly?: boolean }) {
  const id = useId();
  return (
    <FieldShell label={label} hint={hint} error={error} disabled={disabled} htmlFor={id}>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(e: ChangeEvent<HTMLInputElement>) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        readOnly={readOnly}
        className="w-full bg-bg border border-border rounded px-2 py-1.5 text-sm mono focus:outline-none focus:border-accent disabled:opacity-50"
      />
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

export function NumberField({ label, hint, error, disabled, value, min, max, step, onChange }: NumberFieldProps) {
  const id = useId();
  return (
    <FieldShell label={label} hint={hint} error={error} disabled={disabled} htmlFor={id}>
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

export function SelectField({ label, hint, error, disabled, value, options, onChange }: SelectFieldProps) {
  const id = useId();
  return (
    <FieldShell label={label} hint={hint} error={error} disabled={disabled} htmlFor={id}>
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

export function CheckboxField({ label, hint, error, disabled, checked, onChange }: CheckboxFieldProps) {
  const id = useId();
  return (
    <FieldShell label={label} hint={hint} error={error} disabled={disabled} htmlFor={id} inline>
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
  htmlFor,
  inline = false,
  children,
}: FieldBaseProps & { htmlFor: string; inline?: boolean; children: ReactNode }) {
  return (
    <div className={`flex ${inline ? 'flex-row items-center gap-2' : 'flex-col gap-1'} ${disabled ? 'opacity-50' : ''}`}>
      <label htmlFor={htmlFor} className="text-[10px] uppercase text-text-dim tracking-wider">
        {label}
      </label>
      <div className={`${inline ? 'flex-1' : 'w-full'} ${disabled ? 'pointer-events-none' : ''}`}>{children}</div>
      {hint && !error && <span className="text-[10px] text-text-muted">{hint}</span>}
      {error && <span className="text-[10px] text-red">{error}</span>}
    </div>
  );
}
