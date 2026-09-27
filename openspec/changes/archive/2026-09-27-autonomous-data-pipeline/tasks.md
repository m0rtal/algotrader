# Tasks: Autonomous Data Pipeline

Numbered checklist grouped by spec Requirement. Each task ends with
the test that proves it.

## Section A: Worker `live` mode + heartbeat (Req: Autonomous Pipeline Liveness)

- [ ] **Task A.1** — `apps/api/worker.py`: add `live` subcommand that
  runs `daily` chain in a loop with configurable sleep interval
  (default 1800s) and bounded retry (60s → 300s → 900s, cap 1800s).
  Test: `tests/test_worker_live_mode.py::test_live_loop_runs_cycle_then_sleeps`.
- [ ] **Task A.2** — `apps/api/src/algotrader_api/db/migrations/024_pipeline_heartbeat.sql`:
  CREATE TABLE `pipeline_heartbeat` (worker_pid, phase, last_bar_ts,
  updated_at) + `pipeline_runs` (cycle_id, started_at, finished_at,
  rc, stale_2d_count). Apply via existing migration runner.
  Test: existing `test_migrations_apply.py` passes; new test
  `test_pipeline_heartbeat_table.py::test_heartbeat_row_written`.
- [ ] **Task A.3** — Worker `live` mode writes heartbeat every 30s
  and `pipeline_runs` row per cycle.
  Test: `tests/test_worker_live_mode.py::test_heartbeat_writes`.
- [ ] **Task A.4** — `scripts/cron_liveness_check.sh`: kill worker if
  `pipeline_heartbeat.updated_at` > 5 min old. Trigger immediate
  cycle if `stale_2d_count > 100`.
  Test: `tests/test_cron_liveness_check.sh` (shell unit test).

## Section B: Auto-recovery in `build_features` (Req: Auto-recovery fetches bonds)

- [ ] **Task B.1** — `apps/api/src/algotrader_api/ml/features.py`:
  replace `client.get_historical_bonds(...)` call inside the bond
  recovery path with `client.get_candles(figi, from_, to_)` (the
  method that exists on `RealTinkoffClient`).
  Test: existing `test_backfill_bonds_to_depth.py` updated to
  mock `get_candles` instead of `get_historical_bonds` (per
  coverage-and-quality ADAPT-1).
- [ ] **Task B.2** — New integration test
  `tests/test_features_auto_recovery_uses_candles.py` that exercises
  the auto_recovery path against a stubbed `RealTinkoffClient`
  (not MagicMock) and asserts `get_candles` is called.
  Test is the deliverable — must be GREEN before B.1 merges.

## Section C: Status API exposes liveness (Req: Pipeline Status API)

- [ ] **Task C.1** — `apps/api/src/algotrader_api/routes/admin.py`:
  extend `/api/admin/backfill/status` response with
  `last_cycle_age_seconds` (computed from `pipeline_runs.finished_at`).
  Test: `tests/test_admin_status.py::test_last_cycle_age_seconds`.

## Section D: UI red banner (Req: UI surfaces stale pipeline)

- [ ] **Task D.1** — `apps/web/src/components/StatusBanner.tsx`: new
  component with variants red/yellow/none based on
  `stale_2d_count` and `last_cycle_age_seconds`.
- [ ] **Task D.2** — Mount `<StatusBanner>` in `Topbar.tsx` (so it
  survives route changes). Use the same React Query key as
  `/api/admin/backfill/status` (no duplicate fetches).
- [ ] **Task D.3** — Add `StatusBanner.test.tsx` covering all three
  scenarios (red, yellow, hidden).

## Section E: Operational wiring

- [ ] **Task E.1** — Update crontab: replace
  `0 20 * * * cron_daily_refresh.sh` with
  `@reboot bash /home/hermes/algotrader/scripts/algotrader-supervisor.sh algotrader-live ...`.
  Keep cron `0 20 * * *` as a single belt-and-braces cycle in case
  `live` mode stops cycling.
- [ ] **Task E.2** — Add cron `*/2 * * * * cron_liveness_check.sh`.

## Section F: End-to-end smoke

- [ ] **Task F.1** — Live verification: kill worker mid-cycle → confirm
  watchdog kills + supervisor restarts within 90s → confirm cycle
  finishes → confirm UI banner hides.
- [ ] **Task F.2** — Browser smoke: open `192.168.1.101:5173` in
  Playwright (per 2026-09-23 user policy) and screenshot the Topbar
  banner state.

## Out of scope
- Tinkoff 30s timeout for sparse bonds (ADAPT-11, separate
  priority-queue-redesign change).
- Replacing `populate_expected_bars.py` (called once per day at startup).
- ML training pipeline integration.
