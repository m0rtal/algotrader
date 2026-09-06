import type { ReactNode } from 'react';

interface SectionProps {
  title: string;
  description?: string;
  onSave?: () => void;
  saving?: boolean;
  dirty?: boolean;
  disabled?: boolean;
  children: ReactNode;
}

export function Section({
  title,
  description,
  onSave,
  saving = false,
  dirty = false,
  disabled = false,
  children,
}: SectionProps) {
  return (
    <section className="bg-surface-2 border border-border rounded-md p-4 mb-4">
      <div className="flex items-start justify-between mb-3 gap-4">
        <div>
          <h2 className="text-sm font-semibold mb-1">{title}</h2>
          {description && <p className="text-xs text-text-muted">{description}</p>}
        </div>
        {onSave && (
          <button
            type="button"
            onClick={onSave}
            disabled={!dirty || saving || disabled}
            className="px-3 py-1.5 text-xs rounded bg-accent text-white disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 shrink-0"
          >
            {saving ? 'Сохранение…' : 'Сохранить'}
          </button>
        )}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">{children}</div>
    </section>
  );
}
