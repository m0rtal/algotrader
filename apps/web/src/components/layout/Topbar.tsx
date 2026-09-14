import { useUiStore } from '@stores/uiStore';

export function Topbar() {
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  return (
    // Each topbar item is `whitespace-nowrap` + `shrink-0` so the
    // value never wraps or truncates inside its own pill. The flex
    // container has `min-w-0` so it can shrink; siblings hide at
    // narrower breakpoints in this priority order:
    //   ≥ sm  (≥ 640px)  : Сессия + IMOEX + Sandbox
    //   ≥ md  (≥ 768px)  : + Обновлено
    // Below sm only IMOEX stays (the most important number on a
    // trading dashboard); the /settings link stays as the anchor
    // on the right at every viewport size.
    <div className="flex items-center justify-between px-3 sm:px-5 h-12 bg-surface border-b border-border gap-2 sm:gap-4 min-w-0">
      <div className="flex items-center gap-2 sm:gap-2.5 font-semibold text-sm shrink-0">
        {/* Hamburger toggles — visible only on <lg where Sidebar/Rail
            live inside drawers. aria-controls wires the buttons to
            the drawer panels (Sidebar / RightRail). */}
        <button
          type="button"
          onClick={toggleSidebar}
          aria-label="Открыть панель вселенной"
          aria-controls="dashboard-sidebar"
          className="lg:hidden px-2 py-1 text-text-muted hover:text-text focus-visible:text-accent"
        >
          ☰
        </button>
        <span className="w-2 h-2 rounded-full bg-green shadow-[0_0_6px_var(--color-green)] animate-pulse shrink-0" />
        <span className="whitespace-nowrap">ALGOTRADER</span>
        <span className="text-text-muted mono font-normal text-xs whitespace-nowrap hidden sm:inline">· MOEX</span>
      </div>
      <div className="flex gap-2 sm:gap-4 lg:gap-5 text-xs text-text-muted min-w-0 items-center">
        <span className="hidden sm:flex items-center gap-1.5 whitespace-nowrap shrink-0">
          <span>Сессия</span>
          <span className="text-text mono">CLOSED</span>
        </span>
        <span className="flex items-center gap-1.5 whitespace-nowrap shrink-0">
          <span>IMOEX</span>
          <span className="text-green mono">3 142.8</span>
          <span className="text-green mono">+0.42%</span>
        </span>
        <span className="hidden md:flex items-center gap-1.5 whitespace-nowrap shrink-0">
          <span>Обновлено</span>
          <span className="text-text mono">19:34 МСК</span>
        </span>
        <span className="hidden sm:inline whitespace-nowrap shrink-0">Sandbox</span>
        <a
          href="/settings"
          className="shrink-0 text-text-muted hover:text-text text-base px-2 py-1 focus-visible:text-accent"
          title="Настройки"
          aria-label="Open settings"
        >
          ⚙
        </a>
      </div>
    </div>
  );
}
