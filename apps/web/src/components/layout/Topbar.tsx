export function Topbar() {
  return (
    <div className="flex items-center justify-between px-5 h-12 bg-surface border-b border-border gap-4 min-w-0">
      <div className="flex items-center gap-2.5 font-semibold text-sm shrink-0">
        <span className="w-2 h-2 rounded-full bg-green shadow-[0_0_6px_var(--color-green)] animate-pulse" />
        <span>ALGOTRADER</span>
        <span className="text-text-muted mono font-normal text-xs">· MOEX</span>
      </div>
      <div className="flex gap-5 text-xs text-text-muted min-w-0">
        <span className="hidden sm:inline">
          Сессия <span className="text-text mono ml-1.5">CLOSED</span>
        </span>
        <span className="min-w-0">
          IMOEX <span className="text-green mono ml-1.5">3 142.8</span>{' '}
          <span className="text-green mono ml-1">+0.42%</span>
        </span>
        <span className="hidden md:inline">
          Обновлено <span className="text-text mono ml-1.5">19:34 МСК</span>
        </span>
        <span className="shrink-0">Sandbox</span>
        <a
          href="/settings"
          className="shrink-0 text-text-muted hover:text-text text-base"
          title="Настройки"
          aria-label="Open settings"
        >
          ⚙
        </a>
      </div>
    </div>
  );
}
