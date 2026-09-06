import { useQuery } from '@tanstack/react-query';
import { api } from '@lib/api';
import {
  BarsSeriesSchema,
  FeatureImportanceSchema,
  KpiSchema,
  ModelSchema,
  PipelineStepSchema,
  PortfolioSchema,
  RegimeSchema,
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
      if (!symbol) throw new Error('symbol required');
      const data = await api<unknown>(`/bars/${symbol}`);
      return BarsSeriesSchema.parse(data);
    },
    enabled: !!symbol,
  });
}
