# Design: add-backfill-universe-and-history

## Stack

- **SDK:** `t-tech-investments==1.49.3` (existing, no change)
- **Backend:** FastAPI existing, plus a new router + worker entry-point
- **Database:** existing SQLite WAL — two new tables (`instrument_metadata`,
  `ingestion_logs`)
- **Parquet storage:** existing `bars/<symbol>.parquet` files on disk
- **Live progress:** Server-Sent Events (SSE) over plain
  `StreamingResponse` — no SSE library, no WebSockets
- **Scheduling:** systemd timer (existing pattern from `add-data-fetch`,
  extended)
- **Frontend:** React 19 + TanStack Query + new `EventSource` consumer

## Layout

```
apps/api/src/algotrader_api/
  ingestion/
    backfill.py            — NEW: BackfillRunner + BackfillEvent types
    rate_limit.py          — EXTEND: full token bucket per method
    closed_candles.py     — NEW: is_closed_candle() helper
    client.py              — unchanged (Protocol + make_client factory)
    real_client.py         — unchanged (wrapper around t_tech.invest)
  routes/
    backfill.py            — NEW: POST start/stop, GET status, SSE events
  db/
    migrations/
      004_instrument_metadata.sql — NEW
  worker.py                — EXTEND: `python -m algotrader_api.worker backfill`
                              (new entry-point; scheduled/manual modes unchanged)

apps/web/src/features/data/
  BackfillTab.tsx          — NEW
  StatusPanel.tsx          — NEW
  LogsPanel.tsx            — NEW
  ActionsPanel.tsx         — NEW

apps/api/tests/
  test_backfill.py         — NEW: strategy logic + per-ticker flows
  test_rate_limit.py       — NEW: bucket behaviour
  test_closed_candles.py   — NEW: corner cases
  ingestion/
    test_backfill_sandbox.py — NEW: RUN_SANDBOX_INTEGRATION gated

ops/systemd/
  algotrader-backfill.service — NEW
  algotrader-backfill.timer   — NEW
```

## Data Flow

### Backfill state machine

```
                  ┌──────────────┐
                  │   IDLE       │  ←── after completion or stop()
                  └──────┬───────┘
                         │ start()
                         ▼
                  ┌──────────────┐
                  │ DISCOVERING  │  ←── fetch universe from Tinkoff,
                  └──────┬───────┘      upsert into `instruments`
                         │
                         ▼
                  ┌──────────────┐
                  │ BACKFILLING  │  ←── for each instrument:
                  └──────┬───────┘      decide strategy,
                         │              call client.get_candles(),
                         │              filter is_complete=True,
                         │              write to parquet,
                         │              update instrument_metadata
                         │
              ┌──────────┴─────────┐
              ▼                    ▼
        ┌──────────┐        ┌──────────┐
        │ STOPPING │        │   DONE   │
        └──────────┘        └──────────┘
              │
              ▼
        ┌──────────┐
        │   IDLE   │
        └──────────┘
```

### Per-ticker strategy

```
def decide_strategy(instrument_metadata_row):
    if row is None:
        # New ticker — never backfilled.
        return ("full", settings.history_years_ago(), today)

    last_bar_ts = row["last_bar_ts"]
    today = utc_today()

    if last_bar_ts is None:
        # Row exists but never produced a bar (rare — partial failure).
        return ("full", settings.history_years_ago(), today)

    days_since = (today - last_bar_ts).days
    if days_since > settings.incremental_threshold_days:  # default 2
        # Stale enough that we want to backfill the gap explicitly.
        return ("incremental", last_bar_ts + timedelta(days=1), today)

    # Fresh — skip; the daily timer will catch it tomorrow.
    return ("skip", None, None)
```

### Rate limit

```
class TokenBucket:
    """Per-method token bucket at 14 req/min."""
    capacity = 14
    refill_per_sec = 14 / 60.0   # 0.233...

    async def acquire(self, method: str) -> None:
        bucket = self.buckets[method]
        while True:
            now = monotonic()
            elapsed = now - bucket.last_refill
            bucket.tokens = min(
                self.capacity,
                bucket.tokens + elapsed * self.refill_per_sec,
            )
            bucket.last_refill = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return
            await asyncio.sleep(self._seconds_until_one_token())

async def call_with_retry(call, method):
    await bucket.acquire(method)
    try:
        return await call()
    except TinkoffError as e:
        if e.code == RESOURCE_EXHAUSTED or e.code == 429:
            # Back off exponentially. Reduce per-method cap to 10
            # for 5 minutes, then restore.
            bucket.capacity = 10
            bucket.bump_backoff_until(now + 300)
            await asyncio.sleep(compute_backoff(method))
            return await call_with_retry(call, method)
        raise
```

The bucket state is per-method and shared across all callers in the
worker process. Single-writer MVP — no Redis coordination.

### SSE event stream

```
class BackfillEvent:
    type: str         # "status" | "log" | "ticker_progress" | "done"
    run_id: int
    ts: str           # ISO
    payload: dict     # type-specific

# Server side
@router.get("/backfill/events")
async def stream_events():
    async def gen():
        q = subscribe_to_runner(runner)
        try:
            while True:
                ev = await q.get()
                yield f"event: {ev.type}\ndata: {ev.json()}\n\n"
        finally:
            unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream")
```

The runner keeps a bounded `deque(maxlen=100)` of recent events.
SSE subscribers receive from this deque on connect (catch-up), then live
updates.

### Storage

```
data/state.db
  settings             (existing)
  signals               (existing)
  trades                (existing)
  portfolio             (existing)
  logs                  (existing)
  instruments           (existing)
  pipeline              (existing)
  secrets               (existing)
  instrument_metadata   (NEW: figi, last_bar_ts, last_backfilled_at,
                          total_bars, last_run_status, last_run_at,
                          last_error)
  ingestion_logs        (NEW: ts, run_id, level, figi, message)

data/bars/<ticker>.parquet (existing — backfill writes here)
```

The migrations are forward-only and idempotent (`CREATE TABLE IF NOT EXISTS`).
Adding columns to `instrument_metadata` after first backfill would
require a `004b_add_backfill_columns.sql` — kept out of scope for this
change.

### UI integration

The Backfill tab replaces nothing — it's a new section between
"Бэктест" and "Бары". Three sub-panels stacked vertically:

1. **StatusPanel** — shows current run id, state (idle/running/stopped/done),
   progress bar with `tickers_done / tickers_total`, last run summary
   (timestamp, total bars, errors).
2. **PerTickerTable** — table of all instruments with: ticker, figi,
   status (pending/in_progress/done/skipped/error), bars written,
   last_bar_ts.
3. **LogsPanel** — tail of `ingestion_logs` table, latest at top, scroll
   to bottom on new entry. Filterable by level.

The status panel polls `GET /api/admin/backfill/status` every 5s.
The logs panel uses `EventSource('/api/admin/backfill/events')` with
exponential reconnect.

### Concurrency

`instrument_metadata` row update is wrapped in `BEGIN IMMEDIATE` —
SQLite acquires the reserved lock immediately, blocking any other
writer. This serialises per-row writes across the FastAPI process
and the backfill worker process, so two `start` calls in quick
succession can never both write the same ticker.

A single `BackfillRunner.run()` is the only writer at a time. The
`routes/backfill.py` exposes `start` and `stop`; `start` is refused
with HTTP 409 if a run is already active. Stop is a graceful
cooperative cancellation: it sets a flag the runner checks between
tickers.

## Risks

1. **Tinkoff `from_` keyword conflict in `GetCandlesRequest`.** Trivial
   — wrapper already handles it.

2. **`candle.is_complete` is the SDK's name today.** If the SDK renames
   it (it's a proto field), our `is_closed_candle` is the only place
   that breaks. One-file fix.

3. **SSE through Caddy / reverse proxy.** Some proxies buffer SSE and
   break the streaming model. Documentation: SSE requires
   `Cache-Control: no-cache`, `X-Accel-Buffering: no`, and a long
   `keep-alive` timeout. We set all three explicitly.

4. **`asyncio` event loop must survive across the entire run.** If
   the FastAPI process restarts mid-backfill, the `RUNNING` task dies.
   The persistent systemd mode runs the worker in a separate process
   so the API can restart without killing the backfill.

5. **SQLite write contention.** Per-ticker writes are serialized via
   `BEGIN IMMEDIATE` but reads (the API serving bars) still use WAL
   mode and are unblocked. Single-writer throughput on SQLite is
   ~500 writes/sec which is plenty for 250 tickers.

## Decision Log

- **Persistent worker over in-process task** (user) — chosen because
  the run is too long for a single request/response, and we want
  resumability on API restart.
- **SSE over WebSocket** (engineer) — SSE is one-directional (server
  → client), fits the use case, works through `EventSource` natively,
  and is simpler than WebSockets (no upgrade handshake, no auth in
  the WS URL).
- **in-memory rate-limit bucket over Redis** (engineer) — MVP is
  single-worker. Documented upgrade path in the proposal.
- **`instrument_metadata` separate table over `instruments.last_bar_ts`
  column** (user) — keeps `instruments` table lean (it's already wide
  with FIGI / class / sector / etc.), and metadata about the backfill
  lifecycle is conceptually separate from "this is the instrument
  record".
- **NEW `BackfillRunner` class vs adding to existing pipeline.py**
  (engineer) — the lifecycle (start → discover → backfill → done) is
  fundamentally different from the existing single-shot
  `discover_universe` + `run_bars_phase` flow. Reusing pipeline.py
  would conflate the two. Separate module.
- **`closed_candles.py` helper over inlining `is_complete` checks**
  (Ponytail) — single source of truth for the "what counts as a
  closed bar" rule. Currently it's `candle.is_complete` but if we
  later want to filter on `candle.time < today_utc` as a safety net,
  one place to update.

## Spec delta (for `data-fetch/spec.md`)

### ADDED Requirement: Universe + History Backfill

The system SHALL provide a persistent backfill subsystem that:

- Populates the `instruments` table from the broker sandbox on first run
  (shares, bonds, ETFs, futures, options).
- For each instrument, fetches all available historical daily bars up to
  the configured `history_years` (default 5) when no `instrument_metadata`
  row exists, or when `last_bar_ts` is older than
  `incremental_threshold_days` (default 2).
- Writes only `is_complete == True` candles to the per-ticker parquet.
- Surfaces live progress via `GET /api/admin/backfill/events` SSE stream
  and persisted history via `GET /api/admin/backfill/status` polling.
- Logs every operation (rate-limit delays, per-ticker success/failure,
  errors) to the `ingestion_logs` table.
- Refuses a second `start` while a run is active (HTTP 409).
- Respects Tinkoff rate limits at 14 req/min per method (token bucket
  with exponential backoff on RESOURCE_EXHAUSTED / HTTP 429).
- Is scheduled daily via systemd timer (`algotrader-backfill.timer`)
  at 02:00 MSK, and runnable on demand via
  `POST /api/admin/backfill/start` or
  `python -m algotrader_api.worker backfill`.
