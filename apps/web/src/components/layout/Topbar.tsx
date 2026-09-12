import { useUiStore } from '@stores/uiStore';

export function Topbar() {
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const toggleRail = useUiStore((s) => s.toggleRail);
  return (
    <div className="flex items-center justify-between px-3 sm:px-5 h-12 bg-surface border-b border-border gap-2 sm:gap-4 min-w-0">
      <div className="flex items-center gap-2 sm:gap-2.5 font-semibold text-sm shrink-0">
        {/* Hamburger toggles — visible only on <lg where Sidebar/Rail
            live inside drawers. aria-controls/aria-expanded wire the
            buttons to the drawer panels (Sidebar / RightRail). */}
        <button
          type="button"
          onClick={toggleSidebar}
          aria-label="Открыть панель вселенной"
          aria-controls="dashboard-sidebar"
          className="lg:hidden px-2 py-1 text-text-muted hover:text-text focus-visible:text-accent"
        >
          ☰
        </button>
        <span className="w-2 h-2 rounded-full bg-green shadow-[0_0_6px_var(--color-green)] animate-pulse" />
        <span>ALGOTRADER</span>
        <span className="text-text-muted mono font-normal text-xs hidden sm:inline">· MOEX</span>
      </div>
      <div className="flex gap-3 sm:gap-5 text-xs text-text-muted min-w-0 items-center">
        <span className="hidden sm:inline">
          Сессия <span className="text-text mono ml-1.5">CLOSED</span>
        </span>
        <span className="min-w-0 truncate">
          IMOEX <span className="text-green mono ml-1.5">3 142.8</span>{' '}
          <span className="text-green mono ml-1">+0.42%</span>
        </span>
        <span className="hidden md:inline">
          Обновлено <span className="text-text mono ml-1.5">19:34 МСК</span>
        </span>
        <span className="hidden sm:inline shrink-0">Sandbox</span>
        <button
          type="button"
          onClick={toggleRail}
          aria-label="Открыть панель модели"
          aria-controls="dashboard-rail"
          className="lg:hidden px-2 py-1 text-text-muted hover:text-text focus-visible:text-accent shrink-0"
        >
          ⚙
        </button>
        <a
          href="/settings"
          className="hidden lg:inline shrink-0 text-text-muted hover:text-text text-base"
          title="Настройки"
          aria-label="Open settings"
        >
          ⚙
        </a>
      </div>
    </div>
  );
}
