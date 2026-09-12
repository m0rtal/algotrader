import { create } from 'zustand';

export type TabId = 'signals' | 'trades' | 'portfolio' | 'backtest' | 'storage' | 'backfill';

interface UiState {
  activeTab: TabId;
  selectedTicker: string | null;
  // Drawer visibility for mobile (< lg). On desktop these are always
  // visible; on mobile they overlay the main column.
  sidebarOpen: boolean;
  railOpen: boolean;
  setActiveTab: (tab: TabId) => void;
  openTicker: (symbol: string) => void;
  closeTicker: () => void;
  toggleSidebar: () => void;
  toggleRail: () => void;
  closeSidebars: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  activeTab: 'signals',
  selectedTicker: null,
  sidebarOpen: false,
  railOpen: false,
  setActiveTab: (tab) => set({ activeTab: tab }),
  openTicker: (symbol) => set({ selectedTicker: symbol, sidebarOpen: false, railOpen: false }),
  closeTicker: () => set({ selectedTicker: null }),
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  toggleRail: () => set((s) => ({ railOpen: !s.railOpen })),
  closeSidebars: () => set({ sidebarOpen: false, railOpen: false }),
}));
