# Proposal: add-backfill-universe-and-history

## Why

The current ingestion flow (commit `add-data-fetch`) only runs once on
demand via `POST /api/admin/fetch`. After it runs, the universe table
is empty (no real broker token was present, and `InMemoryTinkoffClient`
returns zero instruments), and no historical bars are written. The user
now has a real sandbox token in the database and needs:

1. **Universe bootstrap** — populate `instruments` from the live Tinkoff
   sandbox with all MOEX shares, bonds, ETFs, futures, options.
2. **Historical backfill** — for every instrument, fetch up to 5 years
   of daily candles (the `history_years` setting) and write them to
   parquet.
3. **Incremental updates** — on subsequent runs, fetch only bars newer
   than what we already have.
4. **Operator visibility** — current progress, log lines, and per-ticker
   status surfaced through the UI without requiring SSH into the box.

This change adds a new subsystem that owns the backfill lifecycle end
to end: scheduled run, persistent state, rate-limited SDK calls, progress
emission, and resumability on crash.

## What Changes

1. **`db/migrations/004_instrument_metadata.sql`** — new table tracking
   per-instrument backfill state (last bar ts, last run status, total
   bars). Also `ingestion_logs` for operator-visible log lines.
2. **`ingestion/backfill.py`** (NEW) — `BackfillRunner` class that
   owns the lifecycle: iterate instruments, decide per-ticker strategy
   (full vs incremental), issue rate-limited SDK calls, write to
   parquet, emit progress events.
3. **`ingestion/rate_limit.py`** — extended from the existing skeleton
   into a full token bucket per method, 14 req/min (1 below Tinkoff's
   cap). `AdaptiveRetry` already exists; this change makes the bucket
   shared across calls and surfaces backoff state to the operator.
4. **`ingestion/closed_candles.py`** (NEW) — single helper
   `is_closed_candle(candle) -> bool` that wraps `candle.is_complete`.
   Centralised because every writer needs to agree on the rule.
5. **`worker.py`** — new entry point `python -m algotrader_api.worker
backfill` for the persistent systemd-timer-driven mode. Old
   `scheduled` / `manual` modes are unchanged (still per-process).
6. **`routes/backfill.py`** (NEW) — `POST /api/admin/backfill/start`,
   `POST /api/admin/backfill/stop`, `GET /api/admin/backfill/status`,
   `GET /api/admin/backfill/events` (Server-Sent Events stream of
   progress + log lines).
7. **`main.py`** — register the new router. No lifespan change beyond
   the existing FastAPI startup.
8. **Tests:**
   - `tests/test_backfill.py` — strategy logic (full vs incremental)
     against a mocked SDK. ~10 tests.
   - `tests/test_rate_limit.py` — token bucket behaviour, exhaust +
     recover, shared across callers. ~8 tests.
   - `tests/test_closed_candles.py` — 4 corner cases (open vs closed
     vs missing flag).
   - `tests/ingestion/test_backfill_sandbox.py` — gated by
     `RUN_SANDBOX_INTEGRATION=1`, end-to-end against real Tinkoff
     sandbox for one ticker, asserts non-empty bars and a populated
     `instrument_metadata` row.
9. **UI:**
   - `apps/web/src/features/data/` (NEW) — BackfillTab with three
     sections: "Status" (current run progress + per-ticker table),
     "Logs" (scrolling log stream), "Actions" (Start / Stop buttons +
     last run summary).
   - `apps/web/src/lib/hooks.ts` — `useBackfillStatus`,
     `useBackfillEvents` (SSE consumer with auto-reconnect),
     `useStartBackfill`, `useStopBackfill`.
   - `apps/web/src/mocks/handlers.ts` — MSW handlers for the same
     endpoints so dev mode without a backend still renders something
     useful.
   - `apps/web/src/features/shell/AppShell.tsx` — add the Backfill tab
     to the top-level navigation. Order: existing tabs, then Backfill.
10. **`ops/systemd/algotrader-backfill.{service,timer}`** — unit files
    for the persistent mode. Timer fires daily at 02:00 MSK (when
    MOEX is closed). Manual one-shot still available via
    `systemctl start algotrader-backfill.service`.
11. **`docs/superpowers/plans/2026-09-08-backfill.md`** — task-by-task
    implementation plan.
12. **OpenSpec:** this change adds new requirements to the existing
    `data-fetch` capability spec (no new capability — backfill is part
    of data fetching, not a separate domain).

## Impact

- **Backend runtime:** new `instrument_metadata` and `ingestion_logs`
  tables in `state.db`. Existing `instruments` and `bars` tables
  unchanged.
- **Rate-limit behaviour:** a shared in-memory bucket per method
  (process-local). For multi-worker deployments this would need Redis
  (out of scope for this change; documented in design).
- **Daily fresh data:** systemd timer triggers backfill at 02:00 MSK;
  each ticker's `last_bar_ts` ensures we only fetch what's new.
- **Storage:** first backfill of 250 tickers × 5 years daily = ~312k
  bars × ~50 bytes each ≈ 15 MB parquet total. Fits comfortably on
  any host.
- **UI:** new "Backfill" tab between "Бэктест" and "Бары" (so it's
  visually adjacent to the data it manages). Live progress via SSE —
  no polling cost on the server.
- **Frontend MSW:** dev mode still works without backend. The mock
  backfill emits synthetic events on a timer so the UI is exercisable.

## Non-Goals

- **Multi-worker rate-limit coordination.** Single-worker is the MVP.
  Documented in design as a future Redis-backed upgrade.
- **Tick/quote streaming.** Polling once per daily timer is enough.
- **Order book or trade-by-trade history.** This is daily-bar scope.
- **Replacing the existing `worker.py scheduled/manual` modes.** They
  remain for one-off operational runs; the new persistent mode is
  additive.
- **Backfilling from production (`target="production"`).** Sandbox
  only, per user direction 2026-09-07. Production backfill is a
  separate change after paper trading.
- **Auto-start on API startup.** The backfill is a long-running task
  and should not share the API process. Always started via systemd
  unit or explicit `POST /api/admin/backfill/start`.

## Risks

1. **Tinkoff rate limits are per-account, not per-token.** A single
   `python -m algotrader_api.worker backfill` is fine. Two parallel
   backfill workers will burn quota twice as fast. The systemd timer
   is the only scheduled trigger; manual `start` should refuse if a
   run is already active (covered by `_running` set in
   `routes/backfill.py`).

2. **Long-running backfill crosses API restarts.** State lives in the
   `instrument_metadata` table; on restart, the runner resumes from
   `last_bar_ts` for each ticker rather than restarting the universe.
   Mitigated by writing `last_bar_ts` after **every** successful
   per-ticker write, not after the whole run.

3. **SSE connection storms on flaky networks.** The UI uses
   `EventSource` with exponential reconnect; the server side is plain
   `StreamingResponse` with a small in-process pub/sub of recent
   events. No persistence guarantee on dropped events — the UI keeps
   the last 100 in a buffer so a brief reconnect doesn't lose history.

4. **Mock SDK method names drift again.** A previous change (commit
   `63ed3c3`) had to rename every `client.users.get_accounts()` call
   because the SDK switched from kwargs to request objects. Same risk
   here — we wrap every SDK call in the runner so a future rename is
   one file, not the whole codebase.

5. **First backfill may take 30+ minutes for 250 tickers.** Rate
   limit is 14 req/min = ~17 min for candles alone, plus universe
   discovery (~5 endpoints × ~2s each). The UI must show "running"
   state during this window without user confusion.
