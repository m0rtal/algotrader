export const signals = [
  { symbol: 'SBER', side: 'long', price: 312.4, forecast5d: 2.8, confidence: 0.72, strength: 0.72, regime: 'trend', volume: 5200, updatedAt: '2026-09-06T19:34:00+03:00' },
  { symbol: 'GAZP', side: 'long', price: 128.65, forecast5d: 1.9, confidence: 0.64, strength: 0.64, regime: 'trend', volume: 8100, updatedAt: '2026-09-06T19:34:00+03:00' },
  { symbol: 'YNDX', side: 'long', price: 4218, forecast5d: 3.4, confidence: 0.81, strength: 0.81, regime: 'trend', volume: 1200, updatedAt: '2026-09-06T19:34:00+03:00' },
  { symbol: 'LKOH', side: 'long', price: 6542, forecast5d: 2.1, confidence: 0.58, strength: 0.58, regime: 'trend', volume: 920, updatedAt: '2026-09-06T19:34:00+03:00' },
  { symbol: 'NVTK', side: 'long', price: 1089, forecast5d: 1.6, confidence: 0.55, strength: 0.55, regime: 'trend', volume: 2400, updatedAt: '2026-09-06T19:34:00+03:00' },
  { symbol: 'GMKN', side: 'hold', price: 142.8, forecast5d: 0.2, confidence: 0.42, strength: 0.42, regime: 'trend', volume: 1800, updatedAt: '2026-09-06T19:34:00+03:00' },
  { symbol: 'ROSN', side: 'short', price: 498.2, forecast5d: -1.4, confidence: 0.67, strength: 0.67, regime: 'trend', volume: 3100, updatedAt: '2026-09-06T19:34:00+03:00' },
  { symbol: 'SNGS', side: 'short', price: 38.62, forecast5d: -2.2, confidence: 0.71, strength: 0.71, regime: 'trend', volume: 5600, updatedAt: '2026-09-06T19:34:00+03:00' },
  { symbol: 'MAGN', side: 'hold', price: 512.4, forecast5d: -0.3, confidence: 0.38, strength: 0.38, regime: 'trend', volume: 2100, updatedAt: '2026-09-06T19:34:00+03:00' },
  { symbol: 'MTSS', side: 'hold', price: 234.15, forecast5d: 0.5, confidence: 0.45, strength: 0.45, regime: 'trend', volume: 1400, updatedAt: '2026-09-06T19:34:00+03:00' },
] as const;

export const trades = [
  { id: 't1', ts: '2026-09-06T10:35:00+03:00', symbol: 'SBER', side: 'buy', qty: 100, price: 311.2, amount: 311200, pnl: null, strategy: 'XGB v2.3' },
  { id: 't2', ts: '2026-09-06T10:42:00+03:00', symbol: 'GAZP', side: 'buy', qty: 200, price: 128.1, amount: 256200, pnl: null, strategy: 'XGB v2.3' },
  { id: 't3', ts: '2026-09-06T11:08:00+03:00', symbol: 'YNDX', side: 'buy', qty: 10, price: 4198, amount: 419800, pnl: null, strategy: 'XGB v2.3' },
  { id: 't4', ts: '2026-09-06T11:15:00+03:00', symbol: 'LKOH', side: 'sell', qty: 5, price: 6558, amount: 327900, pnl: 800, strategy: 'XGB v2.3' },
  { id: 't5', ts: '2026-09-06T11:42:00+03:00', symbol: 'SNGS', side: 'sell', qty: 500, price: 38.75, amount: 193750, pnl: 650, strategy: 'XGB v2.3' },
  { id: 't6', ts: '2026-09-06T12:20:00+03:00', symbol: 'ROSN', side: 'sell', qty: 50, price: 499.5, amount: 249750, pnl: 650, strategy: 'XGB v2.3' },
  { id: 't7', ts: '2026-09-06T13:05:00+03:00', symbol: 'NVTK', side: 'buy', qty: 20, price: 1085, amount: 217000, pnl: null, strategy: 'XGB v2.3' },
  { id: 't8', ts: '2026-09-06T13:18:00+03:00', symbol: 'GMKN', side: 'buy', qty: 50, price: 142.4, amount: 71200, pnl: null, strategy: 'XGB v2.3' },
  { id: 't9', ts: '2026-09-06T14:00:00+03:00', symbol: 'TATN', side: 'buy', qty: 30, price: 611.5, amount: 183450, pnl: null, strategy: 'XGB v2.3' },
  { id: 't10', ts: '2026-09-06T14:32:00+03:00', symbol: 'CHMF', side: 'sell', qty: 20, price: 1290, amount: 258000, pnl: -600, strategy: 'XGB v2.3' },
  { id: 't11', ts: '2026-09-06T15:10:00+03:00', symbol: 'PLZL', side: 'buy', qty: 10, price: 1638, amount: 163800, pnl: null, strategy: 'XGB v2.3' },
  { id: 't12', ts: '2026-09-06T15:45:00+03:00', symbol: 'MTSS', side: 'buy', qty: 100, price: 233.8, amount: 233800, pnl: null, strategy: 'XGB v2.3' },
] as const;

export const portfolio = {
  cash: 187380,
  invested: 937000,
  total: 1124380,
  longCount: 8,
  shortCount: 4,
  grossExposure: 1.07,
  netExposure: 0.31,
  positions: [
    { symbol: 'SBER', side: 'long', qty: 100, avgPrice: 311.2, price: 312.4, value: 312400, weight: 0.333, pnl: 120 },
    { symbol: 'GAZP', side: 'long', qty: 200, avgPrice: 128.1, price: 128.65, value: 257300, weight: 0.275, pnl: 110 },
    { symbol: 'YNDX', side: 'long', qty: 10, avgPrice: 4198, price: 4218, value: 42180, weight: 0.045, pnl: 200 },
    { symbol: 'NVTK', side: 'long', qty: 20, avgPrice: 1085, price: 1089, value: 21780, weight: 0.023, pnl: 80 },
    { symbol: 'SNGS', side: 'short', qty: 500, avgPrice: 38.75, price: 38.62, value: 19310, weight: 0.021, pnl: 650 },
    { symbol: 'ROSN', side: 'short', qty: 50, avgPrice: 499.5, price: 498.2, value: 24910, weight: 0.027, pnl: 650 },
    { symbol: 'TATN', side: 'long', qty: 30, avgPrice: 611.5, price: 612.3, value: 18369, weight: 0.020, pnl: 24 },
    { symbol: 'CHMF', side: 'short', qty: 20, avgPrice: 1290, price: 1287, value: 25740, weight: 0.027, pnl: -600 },
    { symbol: 'PLZL', side: 'long', qty: 10, avgPrice: 1638, price: 1642, value: 16420, weight: 0.017, pnl: 40 },
    { symbol: 'MTSS', side: 'long', qty: 100, avgPrice: 233.8, price: 234.15, value: 23415, weight: 0.025, pnl: 35 },
    { symbol: 'GMKN', side: 'long', qty: 50, avgPrice: 142.4, price: 142.8, value: 7140, weight: 0.008, pnl: 20 },
    { symbol: 'MAGN', side: 'long', qty: 20, avgPrice: 513.2, price: 512.4, value: 10248, weight: 0.011, pnl: -160 },
  ],
} as const;

export const regime = {
  state: 'trend' as const,
  confidence: 0.78,
  imoexChange: 0.42,
  volatility20d: 14.2,
  breadth: 0.71,
  sinceDate: '2026-09-04',
} as const;

export const model = {
  version: 'v2.3 (walk-fwd)',
  trainWindowMonths: 24,
  trainStart: '2024-09-01',
  trainEnd: '2026-08-31',
  oosAccuracy: 0.572,
  oosSharpe: 1.84,
  ic: 0.081,
  lastTrainDate: '2026-09-01',
  nextTrainDate: '2026-10-01',
} as const;

export const features = [
  { name: 'mom_20d', importance: 0.088 },
  { name: 'rsi_14', importance: 0.072 },
  { name: 'pe_zscore', importance: 0.064 },
  { name: 'sector_rel', importance: 0.058 },
  { name: 'adv_20d', importance: 0.046 },
] as const;

export const pipeline = [
  { name: 'Universe', status: 'ok' as const, detail: '47 tickers' },
  { name: 'Fetch', status: 'ok' as const, detail: '47 tickers' },
  { name: 'Features', status: 'ok' as const, detail: '124 feats' },
  { name: 'Regime', status: 'ok' as const, detail: 'HMM' },
  { name: 'Model', status: 'ok' as const, detail: 'XGBoost' },
  { name: 'Backtest', status: 'ok' as const, detail: 'wf' },
  { name: 'Broker', status: 'idle' as const, detail: 'sandbox' },
] as const;

export const kpis = [
  { label: 'Equity', value: '1 124 380 ₽', sub: '+12 438 ₽ · +1.12%', tone: 'pos' as const },
  { label: 'P&L день', value: '+4 217 ₽', sub: '+0.38%', tone: 'pos' as const },
  { label: 'P&L месяц', value: '+38 902 ₽', sub: '+3.58%', tone: 'pos' as const },
  { label: 'Sharpe (90д)', value: '1.84', sub: 'vs IMOEX 0.62', tone: 'neutral' as const },
  { label: 'Max DD', value: '-7.2%', sub: 'с 2026-07-14', tone: 'neg' as const },
  { label: 'Позиций', value: '12', sub: 'из 47 universe', tone: 'neutral' as const },
] as const;

export const folds = [
  { id: 1, trainStart: '2024-09-01', trainEnd: '2025-08-31', testStart: '2025-09-01', testEnd: '2026-02-28', sharpe: 1.62, cagr: 0.141, winRate: 0.532, trades: 247, maxDd: -0.084 },
  { id: 2, trainStart: '2024-12-01', trainEnd: '2025-11-30', testStart: '2025-12-01', testEnd: '2026-05-31', sharpe: 1.71, cagr: 0.168, winRate: 0.541, trades: 231, maxDd: -0.067 },
  { id: 3, trainStart: '2025-03-01', trainEnd: '2026-02-28', testStart: '2026-03-01', testEnd: '2026-08-31', sharpe: 1.84, cagr: 0.182, winRate: 0.547, trades: 218, maxDd: -0.072 },
  { id: 4, trainStart: '2025-06-01', trainEnd: '2026-05-31', testStart: '2026-06-01', testEnd: '2026-08-31', sharpe: 1.92, cagr: 0.194, winRate: 0.558, trades: 104, maxDd: -0.041 },
] as const;

export const tickers = [
  { symbol: 'SBER', name: 'Сбер Банк', sector: 'Banks', price: 312.4, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 78_000, gaps: 0 },
  { symbol: 'GAZP', name: 'Газпром', sector: 'Energy', price: 128.65, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 76_000, gaps: 0 },
  { symbol: 'LKOH', name: 'Лукойл', sector: 'Energy', price: 6542, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 78_000, gaps: 0 },
  { symbol: 'YNDX', name: 'Яндекс', sector: 'IT', price: 4218, bars: 1180, firstDate: '2022-04-12', lastDate: '2026-09-05', fileSize: 74_000, gaps: 2 },
  { symbol: 'GMKN', name: 'Норникель', sector: 'Metals', price: 142.8, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 76_000, gaps: 0 },
  { symbol: 'NVTK', name: 'НОВАТЭК', sector: 'Energy', price: 1089, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 76_000, gaps: 0 },
  { symbol: 'ROSN', name: 'Роснефть', sector: 'Energy', price: 498.2, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 76_000, gaps: 0 },
  { symbol: 'MTSS', name: 'МТС', sector: 'Telecom', price: 234.15, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 75_000, gaps: 0 },
  { symbol: 'MAGN', name: 'Магнит', sector: 'Retail', price: 512.4, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 76_000, gaps: 0 },
  { symbol: 'SNGS', name: 'Сургутнефтегаз', sector: 'Energy', price: 38.62, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 74_000, gaps: 0 },
  { symbol: 'TATN', name: 'Татнефть', sector: 'Energy', price: 612.3, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 76_000, gaps: 0 },
  { symbol: 'ALRS', name: 'Алроса', sector: 'Metals', price: 62.18, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 74_000, gaps: 0 },
  { symbol: 'CHMF', name: 'Северсталь', sector: 'Metals', price: 1287, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 76_000, gaps: 0 },
  { symbol: 'PLZL', name: 'Полюс', sector: 'Metals', price: 1642, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 75_000, gaps: 0 },
  { symbol: 'PHOR', name: 'Фосагро', sector: 'Materials', price: 5124, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 76_000, gaps: 0 },
  { symbol: 'MOEX', name: 'Мосбиржа', sector: 'Finance', price: 218.4, bars: 1247, firstDate: '2021-09-06', lastDate: '2026-09-05', fileSize: 76_000, gaps: 0 },
] as const;

export const barsByTicker: Record<string, number[]> = {
  SBER: [305, 308, 311, 309, 312, 314, 313, 315, 318, 316, 319, 321, 318, 320, 322, 319, 321, 324, 322, 325, 328, 326, 329, 331, 328, 330, 332, 329, 331, 312.4],
  GAZP: [125, 126, 128, 127, 129, 130, 128, 131, 133, 131, 134, 132, 135, 133, 136, 134, 137, 135, 138, 136, 139, 137, 140, 138, 141, 139, 142, 140, 141, 128.65],
  LKOH: [6200, 6280, 6350, 6320, 6420, 6480, 6450, 6520, 6580, 6540, 6620, 6680, 6650, 6720, 6780, 6750, 6820, 6880, 6850, 6920, 6980, 6950, 7020, 7080, 7050, 7120, 7180, 7150, 7220, 6542],
  YNDX: [3850, 3920, 3980, 4020, 3950, 4050, 4120, 4080, 4150, 4210, 4180, 4250, 4310, 4280, 4350, 4410, 4380, 4450, 4510, 4480, 4550, 4610, 4580, 4650, 4150, 4180, 4200, 4180, 4210, 4218],
  GMKN: [140, 141, 142, 141, 143, 142, 144, 143, 145, 144, 146, 145, 147, 146, 148, 147, 149, 148, 150, 149, 151, 150, 152, 151, 153, 152, 154, 153, 155, 142.8],
  NVTK: [1020, 1035, 1050, 1042, 1060, 1075, 1068, 1085, 1100, 1092, 1110, 1125, 1118, 1135, 1150, 1142, 1160, 1175, 1168, 1185, 1200, 1192, 1210, 1225, 1218, 1235, 1250, 1242, 1260, 1089],
  ROSN: [520, 518, 515, 517, 512, 510, 508, 505, 503, 500, 498, 495, 493, 490, 488, 485, 483, 480, 478, 475, 473, 470, 468, 465, 463, 460, 458, 455, 453, 498.2],
  MTSS: [228, 229, 230, 231, 232, 231, 233, 234, 235, 234, 236, 237, 238, 237, 239, 240, 239, 241, 242, 241, 243, 244, 243, 245, 246, 245, 247, 248, 247, 234.15],
  MAGN: [510, 512, 514, 513, 515, 514, 516, 515, 517, 516, 518, 517, 519, 518, 520, 519, 521, 520, 522, 521, 523, 522, 524, 523, 525, 524, 526, 525, 527, 512.4],
  SNGS: [42, 41.5, 41, 40.5, 40, 39.5, 39, 38.5, 38, 37.5, 37, 36.5, 36, 35.5, 35, 34.5, 34, 33.5, 33, 32.5, 32, 31.5, 31, 30.5, 30, 29.5, 29, 28.5, 28, 38.62],
  TATN: [580, 585, 590, 588, 595, 600, 598, 605, 610, 608, 615, 620, 618, 625, 630, 628, 635, 640, 638, 645, 650, 648, 655, 660, 658, 665, 670, 668, 675, 612.3],
  ALRS: [60, 60.5, 61, 61.5, 62, 61.5, 62, 62.5, 63, 62.5, 63, 63.5, 64, 63.5, 64, 64.5, 65, 64.5, 65, 65.5, 66, 65.5, 66, 66.5, 67, 66.5, 67, 67.5, 68, 62.18],
  CHMF: [1320, 1310, 1300, 1295, 1285, 1275, 1265, 1255, 1245, 1235, 1225, 1215, 1205, 1195, 1185, 1175, 1165, 1155, 1145, 1135, 1125, 1115, 1105, 1095, 1085, 1075, 1065, 1055, 1045, 1287],
  PLZL: [1500, 1520, 1540, 1530, 1560, 1580, 1570, 1600, 1620, 1610, 1640, 1660, 1650, 1680, 1700, 1690, 1720, 1740, 1730, 1760, 1780, 1770, 1800, 1820, 1810, 1840, 1860, 1850, 1880, 1642],
  PHOR: [4500, 4580, 4650, 4620, 4720, 4800, 4780, 4880, 4950, 4920, 5020, 5100, 5080, 5180, 5250, 5220, 5320, 5400, 5380, 5480, 5550, 5520, 5620, 5700, 5680, 5780, 5850, 5820, 5920, 5124],
  MOEX: [200, 203, 206, 205, 209, 212, 211, 215, 218, 217, 221, 224, 223, 227, 230, 229, 233, 236, 235, 239, 242, 241, 245, 248, 247, 251, 254, 253, 257, 218.4],
};

export const logs = [
  { ts: '19:34:12', tone: 'ok' as const, text: 'fetch.py · 47 tickers · 1.2s' },
  { ts: '19:34:14', tone: 'ok' as const, text: 'features.py · 124 features · 3.4s' },
  { ts: '19:34:18', tone: 'ok' as const, text: 'regime.py · HMM trend 0.78 · 0.8s' },
  { ts: '19:34:21', tone: 'ok' as const, text: 'strategy.py · 12 signals · 4.1s' },
  { ts: '19:34:25', tone: 'warn' as const, text: 'backtest.py · OOS Sharpe 1.84 vs prev 1.79' },
  { ts: '19:34:25', tone: 'flat' as const, text: 'broker.py · dry-run (sandbox) · 12 orders queued' },
  { ts: '19:34:25', tone: 'ok' as const, text: 'pipeline ok · next run 2026-09-07 19:30 МСК' },
] as const;

// ─── Settings (mock) ──────────────────────────────────────────────
export const settings = {
  broker: { environment: 'sandbox' as const, tokenLast4: 'ABCD', tokenRedacted: true, accountId: 'ACC-DEMO-001' },
  risk: { maxDrawdownPct: 10, maxPositionSizePct: 20, killSwitchEnabled: false, killSwitchThresholdPct: 15 },
  ml: { modelVersion: 'v2.3', retrainIntervalDays: 30, confidenceThreshold: 0.6, regimeFilter: 'all' as const },
  data: { source: 'tinkoff' as const, cacheTtlMinutes: 60, historyYears: 5, autoFetch: true },
};

// Module-level mutable state for the mocked settings store. Hidden behind an
// object so the handlers can update fields without TS rejecting const export.
export const settingsStore: { values: typeof settings; version: string } = {
  values: settings,
  version: 'v1-2026-09-06-001',
};

