import { create } from 'zustand';

export type TabId = 'signals' | 'trades' | 'portfolio' | 'backtest' | 'storage' | 'backfill';

interface UiState {
  activeTab: TabId;
  selectedTicker: string | null;
  setActiveTab: (tab: TabId) => void;
  openTicker: (symbol: string) => void;
  closeTicker: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  activeTab: 'signals',
  selectedTicker: null,
  setActiveTab: (tab) => set({ activeTab: tab }),
  openTicker: (symbol) => set({ selectedTicker: symbol }),
  closeTicker: () => set({ selectedTicker: null }),
}));
