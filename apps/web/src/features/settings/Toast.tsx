interface ToastProps {
  message: string;
  tone: 'ok' | 'warn' | 'err' | 'info';
  onDismiss: () => void;
}

export function Toast({ message, tone, onDismiss }: ToastProps) {
  const color =
    tone === 'ok'
      ? 'bg-green text-white'
      : tone === 'err'
        ? 'bg-red text-white'
        : tone === 'warn'
          ? 'bg-amber text-bg'
          : 'bg-accent text-white';
  return (
    <div
      className={`fixed bottom-6 right-6 z-50 ${color} px-4 py-2 rounded shadow-lg text-sm font-medium flex items-center gap-3`}
      role="status"
    >
      <span>{message}</span>
      <button
        type="button"
        onClick={onDismiss}
        className="opacity-70 hover:opacity-100"
        aria-label="Закрыть"
      >
        ✕
      </button>
    </div>
  );
}
