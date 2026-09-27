# 2026-09-27 autonomous-data-pipeline — Design

## Goal
Data is always fresh, always matches broker/MOEX, and pipeline self-recovers
without human action. If something does go wrong, the user knows about it
immediately (red UI badge).

## Non-Goals
- Live trading integration
- New ML models
- New data sources (CBR/FRED) — separate change `ru-data-sources`
- Tinkoff-side UI changes

## Today's reality (root cause confirmed via systematic-debugging)
1. **`daily` mode = one-shot cron** — `worker.daily.complete` writes rc=0
   even when trailing fill silently fails (exception caught in
   `_fill_trailing`). Supervisor restarts ONLY on rc≠0. Silent failure
   → no recovery → bars go stale for days.
2. **Auto-recovery in `ml/features.py` is broken in prod** — calls
   `client.get_historical_bonds()` but `RealTinkoffClient` only has
   `get_candles()`. Per coverage-and-quality ADAPT-1: tests mocked
   the method so unit tests pass; prod never had it. Silent fail.
3. **No health monitoring** — when workers die, nobody knows.
   Vite died 24 Sep 20:00, site offline 3 days.
4. **MOEX ISS rate limit + sparse-bond sequencing** — backfill_moex
   processes 3803 figis sequentially; bonds with no ISS data burn
   rate-limit budget.

## Architecture

### A. Always-on worker process (replaces cron `daily`)
- New mode `worker.py live` that runs the chain in a loop:
  migrations → universe_sync → backfill_moex → gap_recovery →
  corporate_actions → dividends → derived
- Loop sleep `N` seconds between cycles (default 1800s = 30 min).
- Each cycle writes rc to `pipeline_runs` table. If cycle fails
  (rc≠0), sleep shorter (60s) and retry; if 3 retries fail, alert.
- **No longer depends on cron.** Replaces `0 20 * * *`.

### B. Heartbeat + liveness
- Worker writes a heartbeat row every 30s: `pipeline_heartbeat`
  table with phase name + last_bar_ts.
- Cron `*/2 * * * *` (every 2 min): `cron_liveness_check.sh`
  - Reads last heartbeat; if older than 5 min → supervisor restart
  - Calls `/api/admin/backfill/status`; if `stale_2d_count > 100`
    → trigger immediate cycle via API.

### C. Auto-recovery in gate (real fix)
- `auto_recovery()` in `ml/features.py` calls
  `backfill_bonds_to_depth()`. That calls
  `client.get_historical_bonds()` (line 2363) — **broken in prod**.
- Fix: replace `get_historical_bonds` call with the equivalent
  `get_candles(figi, from_, to_)` call that exists on
  `RealTinkoffClient`. Same rate-limit semantics, same return shape.
- Add an integration test that exercises the auto_recovery path
  against a stubbed `RealTinkoffClient` (not MagicMock; the
  RealTinkoffClient in prod only has `get_candles`).

### D. UI red badge for stale data
- `GET /api/admin/backfill/status` already returns
  `stale_2d_count` (we saw it in the screenshot).
- New: add `last_cycle_age_seconds` to the response (time since
  pipeline cycle completed).
- Frontend `<StatusBanner>` component:
  - `stale_2d_count == 0` → hidden
  - `stale_2d_count <= 100` → yellow banner "N figis outdated"
  - `stale_2d_count > 100 OR last_cycle_age > 14400s (4h)` →
    red banner "PIPELINE STALE — N figis outdated, last cycle Xh ago"
- Banner appears in Topbar so it's visible on every page.

## Spec delta (adds to `data-quality` capability)
- **Requirement: Autonomous Pipeline Liveness**
  - Scenario: worker must be running continuously (not one-shot)
  - Scenario: stale pipeline triggers automatic restart within 5 min
  - Scenario: pipeline age older than 4h is reported as `STALE`
- **Requirement: Auto-recovery actually fetches bonds**
  - Scenario: stale bond figi triggers `get_candles` call (not
    non-existent `get_historical_bonds`)
  - Scenario: auto-recovery call survives transient Tinkoff errors
    via existing `AdaptiveRetry`
- **Requirement: Pipeline Status API exposes liveness**
  - Scenario: GET `/api/admin/backfill/status` returns
    `last_cycle_age_seconds` and `stale_2d_count`
- **Requirement: UI surfaces stale pipeline immediately**
  - Scenario: red banner appears when `stale_2d_count > 100` OR
    `last_cycle_age > 14400s`

## Risks
1. **`live` mode replaces `daily` cron** — cron is already wired.
   Need to keep cron as a safety net (in case `live` mode process
   itself dies) but main driver is `live` mode.
2. **Tinkoff rate limit** — running pipeline every 30 min uses
   4x quota. Mitigation: only run `gap_recovery` on short cycles;
   full `backfill_moex` only once per day (3 AM).
3. **Live mode rc ≠ 0 retry storm** — if Tinkoff is down, every
   cycle fails. Need backoff: 60s → 300s → 900s, cap at 30 min.
4. **Auto-recovery test fixture drift** — current test mocks
   `get_historical_bonds`; new test must mock `get_candles`.
   Update existing test, do NOT delete it.
5. **Worker heartbeat adds SQLite write load** — every 30s per
   worker. Two workers × 2 writes/min = 4 writes/min = trivial.
6. **liveness check cron uses API** — depends on uvicorn alive.
   If uvicorn dies, liveness fails → existing api-supervisor
   backstop (PR #121) restarts it. Layered defense.

## Tasks (high-level, will go to tasks.md)
1. Add `live` mode to worker.py (loop-based driver).
2. Replace cron `0 20 * * * cron_daily_refresh.sh` with
   `supervisor.sh algotrader-live "python worker.py live"` started
   at boot via `@reboot`. Keep cron as watchdog only.
3. Add `pipeline_heartbeat` table + write in worker.
4. Add `cron_liveness_check.sh` (every 2 min).
5. Fix `auto_recovery()` in features.py to use `get_candles`.
6. Update existing test, add new integration test for prod path.
7. Add `last_cycle_age_seconds` to `/api/admin/backfill/status`.
8. Add `<StatusBanner>` component to Topbar.
9. End-to-end smoke: kill worker mid-cycle → liveness detects →
   restart → pipeline catches up → badge green.

## What's OUT of scope
- Tinkoff 30s timeout for sparse bonds (ADAPT-11 backlog, separate
  priority-queue-redesign change).
- Replacing `populate_expected_bars.py` one-shot with worker inline.
  Worker will just call it once per day at startup.
- Mobile UI changes.
