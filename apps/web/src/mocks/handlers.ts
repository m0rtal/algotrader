import { http, HttpResponse } from 'msw';
import {
  barsByTicker,
  features,
  folds,
  kpis,
  logs,
  model,
  pipeline,
  portfolio,
  regime,
  signals,
  tickers,
  trades,
} from './data';

export const handlers = [
  http.get('/api/kpis', () => HttpResponse.json(kpis)),
  http.get('/api/signals', () => HttpResponse.json(signals)),
  http.get('/api/trades', () => HttpResponse.json(trades)),
  http.get('/api/portfolio', () => HttpResponse.json(portfolio)),
  http.get('/api/regime', () => HttpResponse.json(regime)),
  http.get('/api/model', () => HttpResponse.json(model)),
  http.get('/api/model/features', () => HttpResponse.json(features)),
  http.get('/api/pipeline', () => HttpResponse.json(pipeline)),
  http.get('/api/backtest/folds', () => HttpResponse.json(folds)),
  http.get('/api/tickers', () => HttpResponse.json(tickers)),
  http.get('/api/logs', () => HttpResponse.json(logs)),

  http.get('/api/bars/:symbol', ({ params }) => {
    const symbol = String(params.symbol).toUpperCase();
    const closes = barsByTicker[symbol];
    if (!closes) return new HttpResponse(null, { status: 404 });
    const ticker = tickers.find((t) => t.symbol === symbol);
    if (!ticker) return new HttpResponse(null, { status: 404 });
    const startDate = new Date(ticker.lastDate);
    startDate.setDate(startDate.getDate() - (closes.length - 1));
    const bars = closes.map((close, i) => {
      const ts = new Date(startDate);
      ts.setDate(startDate.getDate() + i);
      const drift = (Math.random() - 0.5) * 0.02 * close;
      return {
        ts: ts.toISOString().slice(0, 10),
        open: +(close * (1 + (Math.random() - 0.5) * 0.005)).toFixed(2),
        high: +(Math.max(close, close * 1.01) + Math.abs(drift)).toFixed(2),
        low: +(Math.min(close, close * 0.99) - Math.abs(drift)).toFixed(2),
        close,
        volume: Math.floor(1_000_000 + Math.random() * 9_000_000),
      };
    });
    return HttpResponse.json({
      symbol,
      count: bars.length,
      first: bars[0]!.ts,
      last: bars[bars.length - 1]!.ts,
      bars,
    });
  }),
];
