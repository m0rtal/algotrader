import { http } from 'msw';
import { passthrough } from './passthrough';

// All MSW handlers are pure passthroughs to the real backend at
// http://127.0.0.1:8000/api. There is no hardcoded mock data; if the
// backend is unreachable, the handler returns 503 and the UI shows an
// honest "—" / "0" / "n/a" empty state. This is what the user asked
// for on 2026-09-10: "Давай откажемся от мок данных. Если данных нет -
// показываем 0 или n/a или -". Mock data would mask real backend
// outages and confuse operators about what is and isn't wired up.

export const handlers = [
  // Read endpoints — passthrough to backend
  http.get('/api/kpis', ({ request }) => passthrough('kpis', request)),
  http.get('/api/signals', ({ request }) => passthrough('signals', request)),
  http.get('/api/trades', ({ request }) => passthrough('trades', request)),
  http.get('/api/portfolio', ({ request }) => passthrough('portfolio', request)),
  http.get('/api/regime', ({ request }) => passthrough('regime', request)),
  http.get('/api/model', ({ request }) => passthrough('model', request)),
  http.get('/api/model/features', ({ request }) => passthrough('model/features', request)),
  http.get('/api/pipeline', ({ request }) => passthrough('pipeline', request)),
  http.get('/api/backtest/folds', ({ request }) => passthrough('backtest/folds', request)),
  http.get('/api/tickers', ({ request }) => passthrough('tickers', request)),
  http.get('/api/logs', ({ request }) => passthrough('logs', request)),

  // Settings (CRUD) — passthrough. The Settings tab is the one place
  // where dev-mode MSW previously held an in-memory store; now the
  // backend is the single source of truth even in dev.
  http.get('/api/settings', ({ request }) => passthrough('settings', request)),
  http.put('/api/settings', ({ request }) => passthrough('settings', request)),
  http.delete('/api/settings', ({ request }) => passthrough('settings', request)),
  http.put('/api/settings/token', ({ request }) => passthrough('settings/token', request)),

  // Bars (per ticker) — passthrough. Backend returns real DuckDB-served
  // bars for tickers that have been backfilled; for the rest, the backend
  // responds with 404 and the UI shows "Нет данных".
  http.get('/api/bars/:symbol', ({ params, request }) =>
    passthrough(`bars/${params.symbol}`, request),
  ),

  // Backfill controls — passthrough so the UI sees real progress,
  // real pending counts, and the real force-reset endpoint.
  http.post('/api/admin/backfill/start', ({ request }) =>
    passthrough('admin/backfill/start', request),
  ),
  http.post('/api/admin/backfill/stop', ({ request }) =>
    passthrough('admin/backfill/stop', request),
  ),
  http.get('/api/admin/backfill/status', ({ request }) =>
    passthrough('admin/backfill/status', request),
  ),
  http.get('/api/admin/backfill/events', ({ request }) =>
    passthrough('admin/backfill/events', request),
  ),
  http.get('/api/admin/backfill/pending', ({ request }) =>
    passthrough('admin/backfill/pending', request),
  ),
  http.post('/api/admin/backfill/force-reset', ({ request }) =>
    passthrough('admin/backfill/force-reset', request),
  ),
];
