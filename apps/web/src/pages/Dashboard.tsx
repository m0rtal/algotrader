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
  { id: 'storage', label: 'Бары (хранилище)' },
];

export function Dashboard() {
  const active = useUiStore((s) => s.activeTab);
  const setTab = useUiStore((s) => s.setActiveTab);

  return (
    <div className="min-h-screen flex flex-col bg-bg text-text">
      <Topbar />
      <KPIStr />
      <div
        className="grid flex-1 bg-border-soft"
        style={{
          gridTemplateColumns: '240px 1fr 320px',
          gridTemplateRows: '1fr',
        }}
      >
        <div className="bg-bg overflow-hidden">
          <Sidebar />
        </div>
        <div className="bg-bg flex flex-col min-w-0">
          <div className="flex bg-surface border-b border-border px-4">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`px-4 py-2.5 text-xs cursor-pointer border-b-2 ${
                  active === t.id
                    ? 'text-text border-accent'
                    : 'text-text-muted border-transparent hover:text-text'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto">
            {active === 'signals' && <SignalsTab />}
            {active === 'trades' && <TradesTab />}
            {active === 'portfolio' && <PortfolioTab />}
            {active === 'backtest' && <BacktestTab />}
            {active === 'storage' && <StorageTab />}
          </div>
        </div>
        <div className="bg-bg overflow-hidden">
          <RightRail />
        </div>
      </div>
      <LogStrip />
      <TickerDrilldown />
    </div>
  );
}
