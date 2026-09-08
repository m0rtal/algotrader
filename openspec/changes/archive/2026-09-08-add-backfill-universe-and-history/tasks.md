# Tasks: add-backfill-universe-and-history

> Steps use checkbox (`- [ ]`) syntax for tracking.

## Section 1: schema

- [ ] **1.1** Create `apps/api/src/algotrader_api/db/migrations/004_instrument_metadata.sql`:
  - `instrument_metadata` table: `figi TEXT PRIMARY KEY`, `last_bar_ts TEXT`,
    `last_backfilled_at TEXT`, `total_bars INTEGER NOT NULL DEFAULT 0`,
    `last_run_status TEXT`, `last_run_at TEXT`, `last_error TEXT`.
  - `ingestion_logs` table: `id INTEGER PRIMARY KEY AUTOINCREMENT`,
    `ts TEXT NOT NULL`, `run_id INTEGER NOT NULL`, `level TEXT NOT NULL`,
    `figi TEXT`, `message TEXT NOT NULL`.
  - Index on `ingestion_logs(ts DESC)` and `ingestion_logs(run_id, ts DESC)`.

- [ ] **1.2** Verify migrations run automatically on backend startup via
      the existing `run_migrations(path, migrations_dir)` call in `main.py`.
      Expected: `instrument_metadata` and `ingestion_logs` tables created
      in `state.db` after first startup with this change.

## Section 2: closed-candle helper

- [ ] **2.1** Create `apps/api/src/algotrader_api/ingestion/closed_candles.py`
      exporting `is_closed_candle(candle) -> bool`.

- [ ] **2.2** Add `tests/test_closed_candles.py` with 4 cases:
  - closed candle with `is_complete=True` → `True`
  - open candle with `is_complete=False` → `False`
  - candle missing `is_complete` attribute → `True` (defensive default —
    assume closed if SDK field is absent, since we filter on it for
    safety)
  - candle where `is_complete` is `None` → `False`

## Section 3: rate-limit token bucket

- [ ] **3.1** Extend `apps/api/src/algotrader_api/ingestion/rate_limit.py`:
  - `RateLimiter` becomes a per-method token bucket at 14 req/min.
  - `acquire(method)` blocks until a token is available.
  - `AdaptiveRetry` keeps its existing `RESOURCE_EXHAUSTED` /
    HTTP 429 handling and exponential backoff (cap 60s).
  - On throttle, drop per-method cap to 10 req/min for 5 minutes,
    then restore. Add an `unhealthy_methods` set that surfaces to the
    operator via the runner's progress events.

- [ ] **3.2** Add `tests/test_rate_limit.py` with 8 tests:
  - bucket starts at capacity
  - `acquire()` decrements and refills at 14/60 per sec
  - 14 rapid `acquire()`s succeed, 15th sleeps until refill
  - multiple methods have independent buckets
  - throttle event reduces per-method cap
  - throttle auto-restores after 5 minutes (use clock injection)
  - retry wraps the call and recovers from a transient error
  - retry gives up after max attempts and raises

## Section 4: backfill runner

- [ ] **4.1** Create `apps/api/src/algotrader_api/ingestion/backfill.py`:
  - `BackfillState` enum: `IDLE`, `DISCOVERING`, `BACKFILLING`, `STOPPING`, `DONE`.
  - `BackfillEvent` dataclass: `type`, `run_id`, `ts`, `payload`.
  - `BackfillRunner` class:
    - constructor takes `client_factory`, `db_path`, `bars_dir`,
      `event_sink` (callable that broadcasts events).
    - `run(history_years, incremental_threshold_days, run_id) -> None`
      is the async entry point. Drives the state machine.
    - `stop()` flips a flag; runner checks it between tickers.
    - Internal helpers (each unit-testable):
      - `_discover_universe()` — calls `client.get_shares() / bonds() / etfs() / futures() / options()`,
        upserts into `instruments` table.
      - `_decide_strategy(figi) -> ("full" | "incremental" | "skip", from, to)`
        based on `instrument_metadata` row.
      - `_backfill_one(figi, strategy, from_, to) -> int` — fetches bars,
        filters closed, writes to parquet, updates `instrument_metadata`.
      - `_log(level, message, figi=None)` — writes to `ingestion_logs`.

- [ ] **4.2** Add `tests/test_backfill.py` (~10 tests, mocked SDK):
  - strategy decision for ticker with no metadata row → ("full", 5y ago, today)
  - strategy decision for ticker with fresh `last_bar_ts` → ("skip", None, None)
  - strategy decision for ticker with stale `last_bar_ts` → ("incremental", last+1d, today)
  - `_discover_universe()` calls all five methods and upserts rows
  - `_backfill_one()` writes to parquet and updates metadata
  - `_backfill_one()` filters closed candles (uses fake candles)
  - `_log()` writes a row with correct fields
  - `stop()` causes the runner to exit between tickers
  - `run()` emits at least one `status`, one `ticker_progress`, and one `done` event
  - errors during per-ticker fetch are logged but don't abort the run

## Section 5: HTTP routes + SSE

- [ ] **5.1** Create `apps/api/src/algotrader_api/routes/backfill.py`:
  - `POST /api/admin/backfill/start` — kick off a backfill. Body
    `{history_years?: int, incremental_threshold_days?: int}`.
    Returns 202 with `run_id`. Returns 409 if a run is already active.
  - `POST /api/admin/backfill/stop` — graceful stop. Returns 202.
  - `GET /api/admin/backfill/status` — current state, run id, progress,
    last run summary.
  - `GET /api/admin/backfill/events` — SSE stream. `Cache-Control: no-cache`,
    `X-Accel-Buffering: no`, `Content-Type: text/event-stream`.
    Each event: `event: <type>\ndata: <json>\n\n`.

- [ ] **5.2** Register the router in `apps/api/src/algotrader_api/main.py`.

- [ ] **5.3** Tests for the routes (use TestClient + mocked runner):
  - start returns 202 with run_id when idle
  - start returns 409 when already running
  - stop returns 202 and sets the stop flag
  - status reflects the underlying runner state
  - events endpoint sets the right SSE headers

## Section 6: worker entry-point

- [ ] **6.1** Extend `apps/api/worker.py`:
  - Add a new `backfill` subcommand to the existing CLI dispatch.
  - `python -m algotrader_api.worker backfill` runs `BackfillRunner.run()` once
    with the systemd-provided settings, then exits 0.
  - Existing `scheduled` and `manual` subcommands unchanged.

## Section 7: sandbox integration test

- [ ] **7.1** Create `apps/api/tests/ingestion/test_backfill_sandbox.py`:
  - Gated by `RUN_SANDBOX_INTEGRATION=1`.
  - Reads token from `db.secrets.get_broker_token(sqlite_path)`.
  - One test: `test_sandbox_backfill_one_ticker` — picks SBER
    (BBG004730N88), runs `_backfill_one()` for 2024-01-01..2024-12-31,
    asserts `len(bars) > 0`, `instrument_metadata` row updated.

## Section 8: UI

- [ ] **8.1** Create `apps/web/src/features/data/BackfillTab.tsx` — top-level
      tab component using the same dark-themed layout as other tabs.

- [ ] **8.2** Create `apps/web/src/features/data/StatusPanel.tsx`:
  - queries `useBackfillStatus()` every 5s (TanStack Query refetch interval).
  - Shows: current state (badge), progress bar `tickers_done / tickers_total`,
    last run summary (timestamp + total bars + error count).

- [ ] **8.3** Create `apps/web/src/features/data/LogsPanel.tsx`:
  - opens `EventSource('/api/admin/backfill/events')` on mount.
  - On reconnect (exponential backoff), re-subscribes.
  - Keeps a `useState` array of last 100 events.
  - Auto-scrolls to bottom on new entry.

- [ ] **8.4** Create `apps/web/src/features/data/ActionsPanel.tsx`:
  - "Start backfill" button → calls `useStartBackfill().mutateAsync(...)`.
  - "Stop" button → `useStopBackfill()`.
  - Disables Start while `status.state === "running"`.
  - Confirmation dialog before start.

- [ ] **8.5** `apps/web/src/lib/hooks.ts`:
  - `useBackfillStatus()` — `useQuery({ queryKey: ['backfill-status'], refetchInterval: 5000 })`.
  - `useBackfillEvents()` — custom hook wrapping `EventSource`, exposes
    last 100 events + connection state.
  - `useStartBackfill()` — `useMutation` for `POST /api/admin/backfill/start`.
  - `useStopBackfill()` — `useMutation` for `POST /api/admin/backfill/stop`.

- [ ] **8.6** `apps/web/src/mocks/handlers.ts`:
  - MSW handlers for the four endpoints above. The `/events` endpoint
    is a regular HTTP response with `text/event-stream` content type
    that emits synthetic events every 3 seconds.

- [ ] **8.7** `apps/web/src/features/shell/AppShell.tsx` — add the
      "Backfill" tab to the top nav between "Бэктест" and "Бары".

- [ ] **8.8** Tests in `apps/web/src/features/data/`:
  - `BackfillTab.test.tsx` — renders all three panels with mocked data.
  - `ActionsPanel.test.tsx` — Start button triggers mutation, Stop
    button is disabled when idle.
  - `LogsPanel.test.tsx` — appends new events from the EventSource
    mock to the visible list.

## Section 9: systemd units

- [ ] **9.1** Create `ops/systemd/algotrader-backfill.service`:
  - `Type=oneshot`, `User=algotrader`, `WorkingDirectory=/opt/algotrader/apps/api`.
  - ExecStart: `uv run python -m algotrader_api.worker backfill`.
  - StandardOutput / StandardError to journald.
  - Hardening: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`,
    `ReadWritePaths=/var/lib/algotrader`, `PrivateTmp=true`.

- [ ] **9.2** Create `ops/systemd/algotrader-backfill.timer`:
  - `OnCalendar=*-*-* 02:00:00 Europe/Moscow`.
  - `Persistent=true` (catch up missed runs).
  - `RandomizedDelaySec=300` (avoid burst).

- [ ] **9.3** Document install steps in `ops/systemd/README.md`:
  - `cp ops/systemd/*.service *.timer /etc/systemd/system/`
  - `systemctl daemon-reload`
  - `systemctl enable --now algotrader-backfill.timer`

## Section 10: validate, commit, archive

- [ ] **10.1** `openspec validate add-backfill-universe-and-history --strict`.

- [ ] **10.2** Apply the spec delta to `openspec/specs/data-fetch/spec.md`:
  - Copy `openspec/changes/add-backfill-universe-and-history/specs/data-fetch/spec.md`
    → `openspec/specs/data-fetch/spec.md`.
  - Drop `(delta)` from title, rename `## ADDED Requirements` →
    `## Requirements`, prepend `## Purpose` paragraph.

- [ ] **10.3** `openspec archive add-backfill-universe-and-history --yes --skip-specs`.

- [ ] **10.4** `git add` all changed files. `git commit -m "feat(data-fetch): persistent backfill subsystem with rate limiting + SSE progress"`.
      Pre-commit hook reindexes codebase-memory.

- [ ] **10.5** `git push origin main`.

- [ ] **10.6** Verify with `gh api repos/m0rtal/algotrader/commits?per_page=2` —
      the new commit SHA appears on `main`.

## Section 11: live smoke test

- [ ] **11.1** Restart backend so the new router and worker entry point
      are loaded.

- [ ] **11.2** `curl http://192.168.1.101:8000/api/admin/backfill/status` —
      expect `{"state":"idle","run_id":null,"tickers_done":0,"tickers_total":0}`.

- [ ] **11.3** `curl -X POST http://192.168.1.101:8000/api/admin/backfill/start
-d '{"history_years":5}' -H "Content-Type: application/json"` —
      expect 202 with `run_id`.

- [ ] **11.4** Poll status every 5 seconds; watch state transition
      IDLE → DISCOVERING → BACKFILLING (progress bar fills) → DONE.

- [ ] **11.5** `curl http://192.168.1.101:8000/api/bars/SBER?from=2024-01-01&to=2024-12-31`
      — expect real bars from Tinkoff sandbox, not the synth seed.

- [ ] **11.6** UI: visit http://192.168.1.101:5173 → Backfill tab →
      see live progress, scroll the logs panel.

## Done means

- [ ] All checklist items above are done
- [ ] Backend coverage ≥92% (relaxed gate, same as current)
- [ ] Added sandbox integration test passes when
      `RUN_SANDBOX_INTEGRATION=1` and a real token is in the DB
- [ ] OpenSpec change archived to
      `openspec/changes/archive/2026-09-08-add-backfill-universe-and-history/`
- [ ] Git commit on `main`, codebase-memory reindexed
- [ ] Live smoke test: universe populates, 250 tickers backfill with
      rate-limit pacing, status endpoint shows `DONE` with non-zero
      `tickers_done` and `total_bars`
- [ ] UI Backfill tab renders with live SSE events
