import { z } from 'zod';

// ─── Primitives ──────────────────────────────────────────────────────
export const TickerSchema = z.object({
  symbol: z.string(),
  name: z.string(),
  sector: z.string(),
  price: z.number(),
  bars: z.number().int().nonnegative(),
  firstDate: z.string(),
  lastDate: z.string(),
  fileSize: z.number().int().nonnegative(),
  gaps: z.number().int().nonnegative(),
});
export type Ticker = z.infer<typeof TickerSchema>;

// ─── Signal ──────────────────────────────────────────────────────────
export const SignalSideSchema = z.enum(['long', 'short', 'hold']);
export type SignalSide = z.infer<typeof SignalSideSchema>;

export const SignalSchema = z.object({
  symbol: z.string(),
  side: SignalSideSchema,
  price: z.number(),
  forecast5d: z.number(),
  confidence: z.number().min(0).max(1),
  strength: z.number().min(0).max(1),
  regime: z.enum(['trend', 'range', 'vol']),
  volume: z.number().int().nonnegative(),
  updatedAt: z.string(),
});
export type Signal = z.infer<typeof SignalSchema>;

// ─── Trade ───────────────────────────────────────────────────────────
export const TradeSideSchema = z.enum(['buy', 'sell']);
export type TradeSide = z.infer<typeof TradeSideSchema>;

export const TradeSchema = z.object({
  id: z.string(),
  ts: z.string(),
  symbol: z.string(),
  side: TradeSideSchema,
  qty: z.number().int().positive(),
  price: z.number().positive(),
  amount: z.number().positive(),
  pnl: z.number().nullable(),
  strategy: z.string(),
});
export type Trade = z.infer<typeof TradeSchema>;

// ─── Position / Portfolio ────────────────────────────────────────────
export const PositionSideSchema = z.enum(['long', 'short']);
export type PositionSide = z.infer<typeof PositionSideSchema>;

export const PositionSchema = z.object({
  symbol: z.string(),
  side: PositionSideSchema,
  qty: z.number().int().positive(),
  avgPrice: z.number().positive(),
  price: z.number().positive(),
  value: z.number().positive(),
  weight: z.number().min(0).max(1),
  pnl: z.number(),
});
export type Position = z.infer<typeof PositionSchema>;

export const PortfolioSchema = z.object({
  cash: z.number().nonnegative(),
  invested: z.number().nonnegative(),
  total: z.number().positive(),
  longCount: z.number().int().nonnegative(),
  shortCount: z.number().int().nonnegative(),
  grossExposure: z.number(),
  netExposure: z.number(),
  positions: z.array(PositionSchema),
});
export type Portfolio = z.infer<typeof PortfolioSchema>;

// ─── Regime ──────────────────────────────────────────────────────────
export const RegimeStateSchema = z.enum(['trend', 'range', 'vol']);
export type RegimeState = z.infer<typeof RegimeStateSchema>;

export const RegimeSchema = z.object({
  state: RegimeStateSchema,
  confidence: z.number().min(0).max(1),
  imoexChange: z.number(),
  volatility20d: z.number(),
  breadth: z.number().min(0).max(1),
  sinceDate: z.string(),
});
export type Regime = z.infer<typeof RegimeSchema>;

// ─── ML Model ────────────────────────────────────────────────────────
export const ModelSchema = z.object({
  version: z.string(),
  trainWindowMonths: z.number().int().positive(),
  trainStart: z.string(),
  trainEnd: z.string(),
  oosAccuracy: z.number().min(0).max(1),
  oosSharpe: z.number(),
  ic: z.number(),
  lastTrainDate: z.string(),
  nextTrainDate: z.string(),
});
export type Model = z.infer<typeof ModelSchema>;

export const FeatureImportanceSchema = z.object({
  name: z.string(),
  importance: z.number().min(0).max(1),
});
export type FeatureImportance = z.infer<typeof FeatureImportanceSchema>;

// ─── Bars ────────────────────────────────────────────────────────────
export const BarSchema = z.object({
  ts: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  volume: z.number().int().nonnegative(),
});
export type Bar = z.infer<typeof BarSchema>;

export const BarsSeriesSchema = z.object({
  symbol: z.string(),
  count: z.number().int().positive(),
  first: z.string(),
  last: z.string(),
  bars: z.array(BarSchema),
});
export type BarsSeries = z.infer<typeof BarsSeriesSchema>;

// ─── Backtest ────────────────────────────────────────────────────────
export const WalkForwardFoldSchema = z.object({
  id: z.number().int().positive(),
  trainStart: z.string(),
  trainEnd: z.string(),
  testStart: z.string(),
  testEnd: z.string(),
  sharpe: z.number(),
  cagr: z.number(),
  winRate: z.number().min(0).max(1),
  trades: z.number().int().nonnegative(),
  maxDd: z.number(),
});
export type WalkForwardFold = z.infer<typeof WalkForwardFoldSchema>;

// ─── KPI ─────────────────────────────────────────────────────────────
export const KpiSchema = z.object({
  label: z.string(),
  value: z.string(),
  sub: z.string().optional(),
  tone: z.enum(['pos', 'neg', 'flat', 'neutral']).default('neutral'),
});
export type Kpi = z.infer<typeof KpiSchema>;

// ─── Pipeline status ────────────────────────────────────────────────
export const PipelineStepSchema = z.object({
  name: z.string(),
  status: z.enum(['ok', 'warn', 'err', 'idle']),
  detail: z.string().optional(),
});
export type PipelineStep = z.infer<typeof PipelineStepSchema>;
