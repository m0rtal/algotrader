# add-data-fetch — proposal

## Why

Backend (`add-backend-api`) exposes `/api/bars/{symbol}` backed by a deterministic synth seed — **no real market data yet**. Without a real Tinkoff feed, every downstream capability (ML, regime detection, backtesting, paper/live trading) operates on fake bars and produces useless signals.

The user has now provided a Tinkoff **sandbox** token (`t.*` prefix, validated by structure). This change wires the backend to the Tinkoff Invest API so bars come from the market, not from `seed/synth.py`.

## What Changes

- **Ingestion worker**: a separate `apps/api/worker.py` process (independent of the FastAPI server). Talks to Tinkoff via `tinkoff-investments` SDK. Loads all instruments (shares, bonds, ETFs, futures, options), fetches daily OHLCV bars per instrument, writes parquet files at `data/bars/<ticker>.parquet`. Replace the synth seed at first start.
- **Token source**: read from `~/.hermes/secrets/tinkoff_token` (chmod 600) at startup, **never** logged, **never** written to `.env` or any tracked file.
- **Scheduler**: systemd user timer at **23:00 MSK** daily (`OnCalendar=*-*-* 23:00:00 Europe/Moscow`). Triggers the worker as a one-shot service. Worker exits when done; failures retry per `Restart=on-failure`.
- **Universe**: dynamic. On first run, fetch all INSTRUMENT_STATUS_BASE instruments across all classes. Cache universe in SQLite (`instruments` table) with `ticker | figi | class | name | currency | lot_size`.
- **Initial fetch**: full history per `historyYears` setting (default 5 years, ~1250 trading days per ticker).
- **Incremental update**: on subsequent runs, fetch bars since `max(date) in <ticker>.parquet`. Append, never replace. Atomic write via `parquet.write_table` to temp file then rename.
- **Rate limiting**: 15 req/min per Tinkoff method (from T-Invest API release notes June 2026 — see Risks). Token-bucket per method via `aiolimiter`. Adaptive backoff on `RESOURCE_EXHAUSTED` (double interval, cap at 60s).
- **Pipeline status**: SQLite `pipeline` table with one row per phase (`discover_universe`, `fetch_bars`). `start_at`, `end_at`, `rows_processed`, `status` (ok/warn/err/idle). Exposed via `/api/pipeline` (new endpoint) so the frontend Pipeline tab shows real state.
- **Hot token rotation**: Pydantic settings re-reads token file on `/api/settings` PUT (broker.token). Worker process re-reads at the top of each cron invocation; live in-process rotation requires a future change (see Non-Goals).
- **Manual trigger**: `POST /api/admin/fetch` (no auth yet) for ad-hoc invocations. Updates `pipeline` table synchronously and returns 202 with run_id.

## Impact

**Affected:**
- `apps/api/src/algotrader_api/seed/synth.py` — kept as fallback when no token is configured (dev experience). Toggled off when worker has populated data.
- `apps/api/src/algotrader_api/main.py` — lifespan registers instruments table migration, no longer seeds synth bars when worker has run at least once.
- `apps/api/src/algotrader_api/db/sqlite.py` — `instruments` and `pipeline` tables added in `002_instruments_and_pipeline.sql`.
- `apps/api/src/algotrader_api/db/duck.py` — `bars` view joins with `instruments` to expose ticker metadata.
- `apps/api/src/algotrader_api/routes/pipeline.py` — **new** route, GET `/api/pipeline` returns current + recent runs.
- `apps/api/src/algotrader_api/routes/admin.py` — **new** route, POST `/api/admin/fetch` triggers manual run.
- `apps/api/worker.py` — **new** entrypoint, separate process.
- `apps/api/pyproject.toml` — adds `tinkoff-investments`, `aiolimiter`, `pendulum` (date math).
- `ops/systemd/` — **new** user timer + service units.
- `apps/web/src/features/pipeline/PipelineTab.tsx` — no change (already reads `/api/pipeline` via MSW handler — same Zod schema works).
- `apps/web/src/mocks/handlers.ts` — keep MSW pipeline handler returning mock status for dev mode.

**Doesn't affect:**
- Frontend `/api/settings` page — broker.token stays server-managed (we display `tokenLast4` only).
- `add-backend-api` observability — worker emits the same structlog + OTel spans.
- Auth — still deferred (manual `/api/admin/fetch` is LAN-bind only).

## Non-Goals

- **Live trading** — sandbox only. `add-live-trading` change is a separate workstream, gated on ≥6 months paper trading.
- **Intraday bars** (1-min, 5-min) — daily only for MVP. Different rate limits and storage shape.
- **Dividends/splits adjustments** — out of scope. Bars are raw OHLCV; `adj_close` column exists in parquet schema but not populated until `add-corporate-actions` lands.
- **In-process token rotation** — token file is re-read at worker startup. To rotate mid-flight, restart the timer (or wait for next cron). Acceptable for daily cadence.
- **Multi-account / portfolio sync** — sandbox account has one default account; positions/orders fetched in `add-portfolio-sync` later.
- **Streaming / WebSocket** — polling only.
- **Backtest engine** — `add-backtest-engine` change, not here.
- **ML/strategy training** — `add-ml-pipeline` change.

## Risks

| Risk | Mitigation |
|---|---|
| **Tinkoff SDK upstream archived** (`Tinkoff/invest-python` GitHub) | Use **GitLab mirror** `opensource.tbank.ru/invest/invest-python` (active v0.2.0-beta111). Pin `tinkoff-investments>=0.2.0,<0.3.0` in lockfile. Falls back to community `tinkoff-invest` if mirror goes down. |
| **Rate limits**: 15 req/min per `Shares/Bonds/Etfs/Futures/Options` method | Token bucket per method, default 14/min (safety margin). Adaptive backoff on 429/RESOURCE_EXHAUSTED (double interval, cap 60s, reset after 5 min of 2xx). |
| **~250 MOEX instruments × ~1250 bars = 312k rows × N classes** | ~1 MB parquet total per class. DuckDB reads union_by_name across all parquets in O(seconds). No partitioning needed at this volume. |
| **Sandbox token leak** in git history | Token stored at `~/.hermes/secrets/tinkoff_token` (chmod 600). Pre-commit credential scanner (`scripts/scan-credentials.py`) blocks commit if `t.<86chars>` literal appears in any staged file. Settings endpoint stores only `tokenLast4` in SQLite. |
| **API outage during fetch** | Worker logs structured failure to `pipeline` table (status=err, rows_processed=<completed_so_far>). systemd timer retries with `Restart=on-failure` up to 3 times within 10 min. Next cron at 23:00 picks up where we left off (incremental by design). |
| **Schema drift between Tinkoff SDK versions** | Pinned `tinkoff-investments>=0.2.0,<0.3.0`. If SDK ships breaking changes within 0.2.x, the worker fails fast at import — better than silent data corruption. |
| **First-run latency**: ~250 tickers × ~1250 days × 1 req/day = 250 sequential requests at 15 req/min = ~17 min | Initial fetch runs as the **first** cron run on a fresh install. Run is async; UI shows progress via `pipeline` table. Acceptable. |
| **Stale cache after token rotation** | Token source is a file; worker re-reads on every cron invocation. To rotate: edit file → next 23:00 run picks it up. |
| **Time zone bugs around bar dates** | Tinkoff returns `google.type.Date` (year/month/day). Store as DATE in parquet (UTC-naive, calendar-day). MOEX trading hours are MSK (UTC+3); document this in spec. |
| **Corporate actions / splits** break backtests | Document as known limitation; parquet `adj_close` column reserved but not yet populated. `add-corporate-actions` change pending. |
