import { create } from 'zustand';
import { DEFAULT_SETTINGS, type Settings } from '@algotrader/shared';

interface SettingsState {
  values: Settings;
  baseline: Settings;
  /** Last known server version, used for optimistic concurrency. */
  version: string | null;
  saving: boolean;
  error: string | null;
  /** Set when initial GET returns 404 — fall back to defaults. */
  loaded: boolean;

  setValues: (values: Settings, version?: string | null) => void;
  update: <K extends keyof Settings>(section: K, patch: Partial<Settings[K]>) => void;
  isDirty: () => boolean;
  reset: () => void;
  setError: (error: string | null) => void;
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  values: DEFAULT_SETTINGS,
  baseline: DEFAULT_SETTINGS,
  version: null,
  saving: false,
  error: null,
  loaded: false,

  setValues: (values, version) =>
    set((state) => ({
      values,
      baseline: values,
      version: version === undefined ? state.version : version,
      loaded: true,
    })),

  update: (section, patch) => {
    const next = { ...get().values, [section]: { ...get().values[section], ...patch } };
    set({ values: next });
  },

  isDirty: () => JSON.stringify(get().values) !== JSON.stringify(get().baseline),

  reset: () => set({ values: get().baseline }),

  setError: (error) => set({ error }),
}));
