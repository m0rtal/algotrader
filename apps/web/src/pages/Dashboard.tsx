import { useUiStore, type TabId } from '@stores/uiStore';
import { SignalsTab } from '@features/signals/SignalsTab';
import { TradesTab } from '@features/trades/TradesTab';
import { PortfolioTab } from '@features/portfolio/PortfolioTab';
import { BacktestTab } from '@features/backtest/BacktestTab';
import { StorageTab } from '@features/storage/StorageTab';
import { Topbar } from '@components/layout/Topbar';
import { KPIStr } from '@components/layout/KPIStr';
import { Sidebar } from '@components/layout/Sidebar';
import { RightRail } from '@components/layout/RightRail';
import { LogStrip } from '@components/layout/LogStrip';
import { TickerDrilldown } from '@components/charts/TickerDrilldown';

const TABS: { id: TabId; label: string }[] = [
  { id: 'signals', label: 'Сигналы' },
  { id: 'trades', label: 'Сделки' },
  { id: 'portfolio', label: 'Портфель' },
  { id: 'backtest', label: 'Бэктест' },
  { id: 'storage', label: 'Бары' },
];

export function Dashboard() {
  const active = useUiStore((s) => s.activeTab);
  const setTab = useUiStore((s) => s.setActiveTab);

  return (
    <div className="h-screen flex flex-col bg-bg text-text overflow-hidden">
      <Topbar />
      <KPIStr />
      {/* Mobile (<lg): single column, right rail below sidebar. Desktop (lg+): 3 columns. */}
      <div className="grid flex-1 bg-border-soft min-h-0 grid-cols-1 lg:[grid-template-columns:240px_minmax(0,1fr)_320px]">
        {/* Sidebar: scroll on desktop; full-height on mobile (sits above content) */}
        <aside className="bg-bg min-h-0 overflow-y-auto hidden lg:block">
          <Sidebar />
        </aside>
        {/* Main */}
        <main className="bg-bg flex flex-col min-w-0 min-h-0">
          <div className="flex bg-surface border-b border-border overflow-x-auto flex-nowrap">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`px-4 py-2.5 text-xs cursor-pointer border-b-2 whitespace-nowrap ${
                  active === t.id
                    ? 'text-text border-accent'
                    : 'text-text-muted border-transparent hover:text-text'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto overflow-x-hidden min-h-0">
            {active === 'signals' && <SignalsTab />}
            {active === 'trades' && <TradesTab />}
            {active === 'portfolio' && <PortfolioTab />}
            {active === 'backtest' && <BacktestTab />}
            {active === 'storage' && <StorageTab />}
          </div>
        </main>
        {/* Right rail: full-height on mobile (below main); fixed column on desktop */}
        <aside className="bg-bg min-h-0 overflow-y-auto hidden lg:block">
          <RightRail />
        </aside>
      </div>
      <LogStrip />
      <TickerDrilldown />
    </div>
  );
}
