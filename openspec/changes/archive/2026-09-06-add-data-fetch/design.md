# add-data-fetch — design

## Stack

- **Python 3.11+** (asyncio for SDK calls)
- **`tinkoff-investments>=0.2.0,<0.3.0`** (T-Bank GitLab mirror, gRPC)
- **`aiolimiter>=1.1.0`** — async token-bucket rate limiting per SDK method
- **`pendulum>=3.0.0`** — robust date math across DST/Moscow tz
- **Protobuf generated types** — bundled with SDK, no separate codegen needed
- **SQLite**: existing `apps/api/src/algotrader_api/db/sqlite.py`
- **DuckDB**: existing `apps/api/src/algotrader_api/db/duck.py`
- **Parquet via pyarrow** — bundled with DuckDB; explicit dep not needed

**Reuses:**
- `apps/api/src/algotrader_api/observability/` — log events, spans, scrubbers (token never logged)
- `apps/api/src/algotrader_api/db/sqlite.py` — execute() with OTel span instrumentation
- `apps/api/src/algotrader_api/db/duck.py` — query helpers for bars
- `packages/shared/src/index.ts` Zod schemas (Zod for frontend; Pydantic mirror in `apps/api/src/.../schemas/api.py` for backend validation)

## File Layout

```
apps/api/
├── worker.py                          # NEW entrypoint — separate process
├── src/algotrader_api/
│   ├── ingestion/                     # NEW module
│   │   ├── __init__.py
│   │   ├── client.py                  # TinkoffClient protocol + factory
│   │   ├── fake_client.py             # InMemoryTinkoffClient for tests
│   │   ├── universe.py                # Discover instruments across all classes
│   │   ├── bars.py                    # FetchCandles orchestrator + atomic write
│   │   ├── rate_limit.py              # Per-method token bucket
│   │   ├── pipeline.py                # Pipeline phase tracking (DB)
│   │   └── retry.py                   # Adaptive backoff for RESOURCE_EXHAUSTED
│   ├── routes/
│   │   ├── pipeline.py                # NEW — GET /api/pipeline
│   │   └── admin.py                   # NEW — POST /api/admin/fetch
│   ├── db/migrations/
│   │   └── 002_instruments_and_pipeline.sql
│   └── seed/synth.py                  # Keep as fallback when no bars exist
└── tests/
    ├── test_ingestion_universe.py
    ├── test_ingestion_bars.py
    ├── test_ingestion_rate_limit.py
    ├── test_ingestion_retry.py
    ├── test_ingestion_fake_client.py
    ├── test_pipeline_route.py
    └── test_admin_route.py

ops/systemd/                             # NEW — service + timer
├── algotrader-fetch.service
└── algotrader-fetch.timer

data/
├── bars/<ticker>.parquet                # existing
├── state.db                              # existing
└── seeds.sqlite                          # NEW — universe cache (or in state.db)
```

## Data Flow

```
                  ┌──────────────────────┐
                  │ systemd timer        │
                  │ 23:00 MSK daily      │
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │ apps/api/worker.py   │
                  │ (one-shot process)   │
                  └──────────┬───────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
   ┌──────────────────────┐      ┌───────────────────────┐
   │ discovery phase      │      │ pipeline phase        │
   │ (load token from     │      │ log start/end/        │
   │  ~/.hermes/secrets/) │      │ status to SQLite      │
   └──────────┬───────────┘      └───────────────────────┘
              │
              ▼
   ┌──────────────────────┐      ┌───────────────────────┐
   │ TinkoffClient        │─────▶│ UniverseService        │
   │ (real or fake)       │      │ GetShares/Bonds/Etfs.. │
   └──────────┬───────────┘      │ → ~250 instruments     │
              │                  └───────────┬───────────┘
              │                              │
              ▼                              ▼
   ┌──────────────────────┐      ┌───────────────────────┐
   │ rate_limit           │      │ SQLite instruments    │
   │ (aiolimiter per      │      │ INSERT OR REPLACE     │
   │  SDK method)         │      └───────────────────────┘
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │ FetchCandles         │
   │ for instrument       │
   │ from=last_date+1     │
   │ to=today             │
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │ DuckDB COPY → temp   │
   │ parquet file         │
   │ rename to <ticker>   │
   └──────────┬───────────┘
              │
              ▼
   ┌──────────────────────┐
   │ data/bars/<ticker>   │
   │ .parquet             │
   │ (atomic replace)     │
   └──────────────────────┘
```

## Storage Schema

### Migration `002_instruments_and_pipeline.sql`

```sql
CREATE TABLE IF NOT EXISTS instruments (
  ticker VARCHAR PRIMARY KEY,        -- e.g. SBER, GAZP
  figi VARCHAR NOT NULL UNIQUE,      -- Tinkoff internal ID
  class VARCHAR NOT NULL,            -- share / bond / etf / future / option
  name VARCHAR NOT NULL,
  currency VARCHAR NOT NULL,         -- RUB / USD / etc
  lot_size INTEGER NOT NULL,
  isin VARCHAR,                      -- bonds/etfs only
  sector VARCHAR,                    -- filled later by ML or manually
  source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_instruments_class ON instruments(class);

CREATE TABLE IF NOT EXISTS pipeline (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phase VARCHAR NOT NULL,             -- 'discover_universe', 'fetch_bars'
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  finished_at TIMESTAMP,
  rows_processed INTEGER DEFAULT 0,
  status VARCHAR NOT NULL DEFAULT 'ok', -- ok / warn / err / idle
  detail TEXT                          -- error message or note
);

CREATE INDEX IF NOT EXISTS idx_pipeline_started ON pipeline(started_at DESC);
```

Bars parquet format unchanged (`data/bars/<ticker>.parquet`).

## API Contract

### New: `GET /api/pipeline`

Returns current pipeline state (last run per phase).

```json
{
  "phases": [
    {"phase": "discover_universe", "status": "ok", "startedAt": "...", "finishedAt": "...", "rowsProcessed": 248},
    {"phase": "fetch_bars", "status": "ok", "startedAt": "...", "finishedAt": "...", "rowsProcessed": 310000}
  ]
}
```

### New: `POST /api/admin/fetch`

Manually trigger a fetch run. Asynchronous — returns immediately with run_id.

```json
{"run_id": 123, "started_at": "..."}
```

When `env ALGOTRADER_FETCH_DISABLED=1`, returns 503 (so prod dev environments can disable it).

### Unchanged

- `/api/bars/{symbol}` — reads parquet (now populated by worker instead of synth seed)
- `/api/settings` — broker.token still tokenLast4-only; underlying token is at `~/.hermes/secrets/tinkoff_token`
- All other endpoints unchanged.

## Token Storage & Rotation

- File path: `~/.hermes/secrets/tinkoff_token`
- File mode: 600 (owner read-only)
- File format: single line, raw token (no `TINKOFF_TOKEN=` prefix)
- Read at worker startup only (synchronous read once per process)
- **Hot rotation**: not supported (worker is one-shot per cron tick). User rotates by editing file → next 23:00 tick reads new token.
- **Live rotation**: future change `add-live-settings-reload` (out of scope).

If the file is missing or unreadable:
- Worker logs `ingest.token.missing` event at ERROR level
- Pipeline phase `discover_universe` marked status=err, detail="token file missing"
- Worker exits with code 2 (systemd treats as failure, retries up to 3×)

## Rate Limiting

T-Invest API release notes (June 2026): **15 requests/minute per method** for `Shares/Bonds/Etfs/Futures/Options`.

Implementation:
- One `aiolimiter.AsyncLimiter(14, 60)` per SDK method (14 = safety margin of 1)
- Wrap each SDK call with `await limiter.acquire()`
- On `RESOURCE_EXHAUSTED` (gRPC code 8) or HTTP 429: enter exponential backoff
  - Double interval each retry, cap at 60s
  - Reset on 5 min of 2xx responses
- Adaptive: per-method bucket is consumed even on errors (don't retry harder)

## Pipeline Phases

Two phases, sequential:

### Phase 1: `discover_universe`

1. Read token file
2. Initialize TinkoffClient
3. Call `Shares.GetShares(instrument_status=INSTRUMENT_STATUS_BASE)` → shares
4. Same for Bonds, Etfs, Futures, Options
5. INSERT OR REPLACE into `instruments` table
6. Mark phase ok / err in pipeline

Target: ~250 instruments. Duration: ~30 sec (rate-limited).

### Phase 2: `fetch_bars`

For each instrument in `instruments` where `class IN ('share', 'etf')` (skip bonds/futures/options for v1):

1. Read `max(date)` from `data/bars/<ticker>.parquet` (None if first run)
2. If first run: from = `today - historyYears_years`; else: from = `last_date + 1 day`
3. Call `MarketData.GetCandles(figi=..., from_=from, to=today, interval=CANDLE_INTERVAL_DAY)`
4. Append to existing parquet (or create new) — DuckDB `COPY ... TO` to temp file then `os.replace` for atomicity
5. Update `pipeline.rows_processed += bars_count`

Target: ~250 × ~1250 days / 1 req = ~17 min cold start; ~30 sec warm (incremental).

## Sandbox vs Live

- Default target: `INVEST_GRPC_API_SANDBOX` (via TinkoffClient `target=` parameter)
- Live target env: `ALGOTRADER_FETCH_TARGET=production` (defer, see Risks)
- For MVP: **sandbox only**. Live is for `add-live-trading` later, gated on paper-trading success.

## Observability

Worker emits the same observability events as the FastAPI process:

- **Log events** (structlog → stdout → OTLP):
  - `ingest.run.start` (run_id, mode: scheduled|manual, target: sandbox)
  - `ingest.universe.start` (phase=discover_universe)
  - `ingest.universe.done` (phase, count=248, duration_ms)
  - `ingest.bars.start` (phase=fetch_bars, instrument_count=248)
  - `ingest.bars.progress` (every 10 instruments: count_done, count_total)
  - `ingest.bars.done` (phase, total_rows, duration_ms, rate_limit_hits=N)
  - `ingest.run.done` (status=ok|err, duration_ms)
  - `ingest.token.missing` (file path NOT included)
  - `ingest.error` (phase, error=str, ticker=str — but **never** token)

- **OTel spans** (manual):
  - `ingest.run` (parent)
    - `ingest.universe` (child)
    - `ingest.bars.<ticker>` (one per instrument)

- **Correlation**: each run gets UUIDv4 run_id as `correlation_id` for log→trace drilling.

## Test Strategy

### Unit (≥95% coverage required)

- `test_universe.py`: FakeTinkoffClient, mock 4 instrument responses, assert INSERT OR REPLACE
- `test_bars.py`: FakeTinkoffClient returns canned candles, assert parquet written atomically
- `test_rate_limit.py`: Burst of 100 requests, assert only 14/min actually call SDK
- `test_retry.py`: RESOURCE_EXHAUSTED → exponential backoff → success on 3rd attempt
- `test_pipeline.py`: Phase lifecycle (start → done), status transitions, error path
- `test_fake_client.py`: Protocol compliance — all methods raise NotImplementedError unless overridden
- `test_admin_route.py`: POST /api/admin/fetch returns 202 with run_id; env disabled → 503
- `test_pipeline_route.py`: GET /api/pipeline returns current state from SQLite

### Integration (manual + CI nightly)

- **Sandbox integration** (no mocks): use real `tinkoff-investments` against sandbox API. Gated by `RUN_SANDBOX_INTEGRATION=1` env var. Runs in CI nightly, skipped on PR. Tests:
  - `test_sandbox_universe.py` — fetch real MOEX shares, assert ≥100 instruments
  - `test_sandbox_bars.py` — fetch SBER last 30 days, assert ≥20 candles

Sandbox tests are slow (network) and require real token — skipped by default in `pnpm test:api`.

## Rollout

1. **Skeleton**: worker.py + ingestion module + tests with FakeTinkoffClient. Coverage ≥95% on all ingestion modules.
2. **Database**: migrations + instruments + pipeline tables.
3. **Routes**: /api/pipeline GET + /api/admin/fetch POST.
4. **Universe**: discover all 4 instrument classes, persist.
5. **Bars**: FetchCandles orchestrator + atomic parquet write + rate limiter.
6. **Systemd units**: install + enable timer; one manual trigger via `systemctl --user start algotrader-fetch.service`.
7. **Frontend switch**: drop MSW pipeline handler — frontend will fetch `/api/pipeline` from real backend (after `VITE_API_BASE_URL` is set).
8. **Sandbox integration tests**: nightly CI run validates end-to-end against real Tinkoff sandbox.
9. **Dev experience**: when no token file exists, worker logs warning and falls back to synth seed (so dev environments without secrets still work).
