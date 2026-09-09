import { http, HttpResponse } from 'msw';
import { SettingsSchema } from '@algotrader/shared';
import {
  barsByTicker,
  features,
  folds,
  kpis,
  logs,
  model,
  persist as persistSettings,
  pipeline,
  portfolio,
  regime,
  signals,
  settingsStore,
  tickers,
  trades,
} from './data';

// Backfill state is held in localStorage so the UI sees a single
// source-of-truth across navigation (start → status → stop).
const BACKFILL_KEY = 'algotrader.mock_backfill_state';
const readBackfillState = (): Record<string, unknown> => {
  try {
    const raw = localStorage.getItem(BACKFILL_KEY);
    return raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
  } catch {
    return {};
  }
};
const writeBackfillState = (state: Record<string, unknown>) => {
  try {
    localStorage.setItem(BACKFILL_KEY, JSON.stringify(state));
  } catch {
    /* ignore quota / disabled storage */
  }
};

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

  // ─── Settings ──────────────────────────────────────────────────────
  http.get('/api/settings', () => {
    // Return a fresh wrapper on every call so TanStack Query detects a
    // new object reference after the broker token is updated. Returning
    // the same mutable object would leave stale tokenLast4 in the cache.
    return HttpResponse.json({
      values: {
        ...settingsStore.values,
        broker: { ...settingsStore.values.broker },
      },
      version: settingsStore.version,
      updatedAt: new Date().toISOString(),
    });
  }),
  http.put('/api/settings', async ({ request }) => {
    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return HttpResponse.json({ error: 'invalid json' }, { status: 400 });
    }
    const parsed = SettingsSchema.safeParse((body as { values?: unknown })?.values);
    if (!parsed.success) {
      return HttpResponse.json(
        { error: 'validation', issues: parsed.error.issues },
        { status: 400 },
      );
    }
    const incomingBody = body as { values: typeof settingsStore.values; version?: string };
    if (incomingBody.version && incomingBody.version !== settingsStore.version) {
      return HttpResponse.json(
        { error: 'version conflict', current: settingsStore.values },
        { status: 409 },
      );
    }
    // Persist. Preserve token if redacted.
    const incoming = incomingBody.values;
    if (incoming.broker.tokenRedacted || !incoming.broker.tokenLast4) {
      settingsStore.values.broker = {
        ...settingsStore.values.broker,
        ...incoming.broker,
        tokenLast4: settingsStore.values.broker.tokenLast4,
      };
    } else {
      settingsStore.values.broker = incoming.broker;
    }
    settingsStore.values.risk = incoming.risk;
    settingsStore.values.ml = incoming.ml;
    settingsStore.values.data = incoming.data;
    settingsStore.version = `v1-${new Date()
      .toISOString()
      .slice(0, 19)
      .replace('T', ' ')
      .replace(/[-:]/g, '-')}`;
    persistSettings(settingsStore.values);
    return HttpResponse.json({
      values: settingsStore.values,
      version: settingsStore.version,
      updatedAt: new Date().toISOString(),
    });
  }),
  http.delete('/api/settings', () => new HttpResponse(null, { status: 204 })),

  // ─── Broker token ──────────────────────────────────────────────────
  // MSW dev fallback: in production the frontend reads VITE_API_BASE_URL
  // and proxies to the real FastAPI backend. The dev path (no env) needs
  // a working mock or the "Save token" button fails with 404.
  http.put('/api/settings/token', async ({ request }) => {
    let body: { token?: unknown };
    try {
      body = (await request.json()) as { token?: unknown };
    } catch {
      return HttpResponse.json({ error: 'invalid json' }, { status: 400 });
    }
    const token = typeof body.token === 'string' ? body.token.trim() : '';
    if (!token) {
      return HttpResponse.json({ error: 'token is required' }, { status: 400 });
    }
    // Mutate in-place AND return a new wrapper so TanStack Query sees a
    // different object reference and re-renders. Without this the cached
    // settings query may keep returning the previous tokenLast4 even
    // after invalidation (TanStack uses Object.is by default).
    settingsStore.values.broker = {
      ...settingsStore.values.broker,
      tokenLast4: token.slice(-4),
      tokenRedacted: true,
    };
    // MSW worker is a service worker — its module-level state is lost on
    // hard reload. Persist to localStorage so dev-mode state survives a
    // page refresh; production hits the real backend instead.
    persistSettings(settingsStore.values);
    settingsStore.version = `v1-${new Date()
      .toISOString()
      .slice(0, 19)
      .replace('T', ' ')
      .replace(/[-:]/g, '-')}`;
    return HttpResponse.json({
      tokenLast4: token.slice(-4),
      tokenRedacted: true,
    });
  }),

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

  // Backfill controls — match real backend routes (`/api/admin/backfill/*`)
  // so dev-mode UI doesn't 404.
  http.post('/api/admin/backfill/start', async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as {
      history_years?: number;
    };
    writeBackfillState({
      state: 'backfilling',
      run_id: Math.floor(Math.random() * 100000),
      tickers_total: tickers.length,
      tickers_done: 0,
      total_bars: 0,
      last_run: null,
      started_at: new Date().toISOString(),
      history_years: body.history_years ?? 1,
    });
    return HttpResponse.json({ run_id: Math.floor(Math.random() * 100000), state: 'started' }, { status: 202 });
  }),

  http.post('/api/admin/backfill/stop', () => {
    writeBackfillState({ ...readBackfillState(), state: 'idle' });
    return HttpResponse.json({ state: 'stopping' });
  }),

  http.get('/api/admin/backfill/status', () => {
    const stored = readBackfillState();
    return HttpResponse.json({
      state: 'idle',
      run_id: null,
      tickers_done: 0,
      tickers_total: 0,
      total_bars: 0,
      ...stored,
    });
  }),

  http.get('/api/admin/backfill/events', () =>
    // MSW doesn't easily stream SSE; return an empty event stream that
    // closes immediately. The UI's useQuery status polling is the
    // primary progress channel.
    new HttpResponse(
      'event: done\ndata: {}\n\n',
      { headers: { 'content-type': 'text/event-stream' } },
    ),
  ),

  // Backfill scheduler-mode helpers — match the backend so dev-mode UI
  // shows realistic pending counts and the reset button works.
  http.get('/api/admin/backfill/pending', () => {
    // Default to all zeros; tests can seed localStorage to override.
    let stored: Record<string, unknown> = {};
    try {
      const raw = localStorage.getItem('algotrader.mock_backfill_state');
      if (raw) stored = JSON.parse(raw);
    } catch {
      stored = {};
    }
    return HttpResponse.json({
      new: 0,
      stale: 0,
      up_to_date: Number(stored.tickers_total ?? 0),
      error: 0,
      total: Number(stored.tickers_total ?? 0),
    });
  }),

  http.post('/api/admin/backfill/force-reset', () => {
    try {
      localStorage.removeItem('algotrader.mock_backfill_state');
    } catch {
      /* ignore */
    }
    return HttpResponse.json({ deleted_rows: 0, next_run: 'full backfill of all instruments' });
  }),

];
