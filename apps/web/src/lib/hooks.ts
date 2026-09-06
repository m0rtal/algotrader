import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';
import { api } from '@lib/api';
import {
  BarsSeriesSchema,
  DEFAULT_SETTINGS,
  FeatureImportanceSchema,
  KpiSchema,
  ModelSchema,
  PipelineStepSchema,
  PortfolioSchema,
  RegimeSchema,
  SettingsSchema,
  SignalSchema,
  TickerSchema,
  TradeSchema,
  WalkForwardFoldSchema,
  type BarsSeries,
  type FeatureImportance,
  type Kpi,
  type Model,
  type PipelineStep,
  type Portfolio,
  type Regime,
  type Settings,
  type Signal,
  type Ticker,
  type Trade,
  type WalkForwardFold,
} from '@algotrader/shared';

function makeHook<T>(schema: { parse: (x: unknown) => T }, key: string) {
  return {
    queryKey: [key],
    queryFn: async () => {
      const data = await api<unknown>(`/${key}`);
      return schema.parse(data);
    },
  };
}

export function useKpis() {
  return useQuery<Kpi[]>(makeHook(KpiSchema.array(), 'kpis'));
}

export function useSignals() {
  return useQuery<Signal[]>(makeHook(SignalSchema.array(), 'signals'));
}

export function useTrades() {
  return useQuery<Trade[]>(makeHook(TradeSchema.array(), 'trades'));
}

export function usePortfolio() {
  return useQuery<Portfolio>(makeHook(PortfolioSchema, 'portfolio'));
}

export function useRegime() {
  return useQuery<Regime>(makeHook(RegimeSchema, 'regime'));
}

export function useModel() {
  return useQuery<Model>(makeHook(ModelSchema, 'model'));
}

export function useModelFeatures() {
  return useQuery<FeatureImportance[]>(makeHook(FeatureImportanceSchema.array(), 'model/features'));
}

export function usePipeline() {
  return useQuery<PipelineStep[]>(makeHook(PipelineStepSchema.array(), 'pipeline'));
}

export function useFolds() {
  return useQuery<WalkForwardFold[]>(makeHook(WalkForwardFoldSchema.array(), 'backtest/folds'));
}

export function useTickers() {
  return useQuery<Ticker[]>(makeHook(TickerSchema.array(), 'tickers'));
}

export function useBars(symbol: string | null) {
  return useQuery<BarsSeries>({
    queryKey: ['bars', symbol],
    queryFn: async () => {
      const data = await api<unknown>(`/bars/${symbol}`);
      return BarsSeriesSchema.parse(data);
    },
    enabled: !!symbol,
  });
}

export interface SettingsResponse {
  values: Settings;
  version: string;
  updatedAt: string;
}

const SettingsResponseSchema = z.object({
  values: SettingsSchema,
  version: z.string(),
  updatedAt: z.string(),
});

export function useSettings() {
  return useQuery<SettingsResponse>({
    queryKey: ['settings'],
    queryFn: async () => {
      try {
        const data = await api<unknown>('/settings');
        return SettingsResponseSchema.parse(data);
      } catch (e) {
        if (e instanceof Error && /404/.test(e.message)) {
          return {
            values: DEFAULT_SETTINGS,
            version: '',
            updatedAt: new Date().toISOString(),
          };
        }
        throw e;
      }
    },
    staleTime: 0,
  });
}

export function useSaveSettings() {
  const qc = useQueryClient();
  return useMutation<SettingsResponse, Error, { values: Settings; version: string }>({
    mutationFn: (body) =>
      api<SettingsResponse>('/settings', { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
    },
  });
}

export function useDeleteSettings() {
  const qc = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: () => api<void>('/settings', { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
    },
  });
}
