import { useState } from 'react';
import { ConfirmDialog } from './ConfirmDialog';

interface Props {
  onReset: () => void;
  busy?: boolean;
}

export function DangerZone({ onReset, busy = false }: Props) {
  const [showResetConfirm, setShowResetConfirm] = useState(false);

  return (
    <section className="bg-surface-2 border border-red rounded-md p-4 mb-4">
      <h2 className="text-sm font-semibold mb-1 text-red">Опасная зона</h2>
      <p className="text-xs text-text-muted mb-3">
        Необратимые действия. Используйте с осторожностью.
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setShowResetConfirm(true)}
          disabled={busy}
          className="px-3 py-1.5 text-xs rounded border border-red text-red hover:bg-red hover:text-white disabled:opacity-40"
        >
          {busy ? 'Сброс…' : 'Сбросить настройки'}
        </button>
      </div>
      <ConfirmDialog
        open={showResetConfirm}
        title="Сбросить все настройки к defaults?"
        body="Это действие необратимо. Все секции вернутся к исходным значениям."
        confirmLabel="Сбросить"
        cancelLabel="Отмена"
        danger
        onConfirm={() => {
          setShowResetConfirm(false);
          onReset();
        }}
        onCancel={() => setShowResetConfirm(false)}
      />
    </section>
  );
}
