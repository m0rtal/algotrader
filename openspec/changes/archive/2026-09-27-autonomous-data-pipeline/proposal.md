# Proposal: Autonomous Data Pipeline

## Why
Bars go stale silently when workers fail or supervisors die. On 2026-09-24
the pipeline stopped writing bars; nobody noticed until 2026-09-27 when
the user reported 3578 figis outdated and the site offline (vite died
without supervisor). Three root causes compound:

1. `worker.py daily` is a one-shot cron job; silent exceptions in
   `_fill_trailing` (Event-loop leak) make the worker exit rc=0 with
   no bars written.
2. `auto_recovery()` in `ml/features.py` (added in coverage-and-quality
   PR #144) calls `client.get_historical_bonds()`, a method that does
   not exist on `RealTinkoffClient` — only on a MagicMock in unit tests.
   Auto-recovery silently no-ops in prod.
3. No liveness monitoring — when vite or workers die, no signal reaches
   the user until they open the UI.

## What
Replace the `daily` cron with an always-on `live` worker process that
self-recovers and reports health. Fix auto-recovery to use the correct
Tinkoff method. Surface stale-pipeline state in the UI immediately.

**Components:**
- A. New `worker.py live` mode that runs the chain in a loop with
  heartbeat and bounded retry.
- B. Liveness layer: heartbeat table + every-2-min cron watchdog.
- C. `auto_recovery()` fix: use `get_candles()` (real method) instead
  of `get_historical_bonds()` (mocked-only).
- D. UI `<StatusBanner>` red badge for stale-pipeline visibility.

## Impact
- `apps/api/worker.py` — add `live` subcommand + heartbeat writes.
- `apps/api/src/algotrader_api/ml/features.py` — fix `auto_recovery()`
  method call.
- `apps/api/src/algotrader_api/ingestion/backfill.py` — bonds depth
  uses `get_candles` (or live mode passes real path).
- `apps/api/src/algotrader_api/db/migrations/024_pipeline_heartbeat.sql`
  — new table `pipeline_heartbeat`, `pipeline_runs`.
- `apps/api/src/algotrader_api/routes/admin.py` — extend
  `/api/admin/backfill/status` with `last_cycle_age_seconds`.
- `scripts/cron_liveness_check.sh` — new script.
- `apps/web/src/components/StatusBanner.tsx` — new component, mounted
  in `Topbar.tsx`.
- cron entry change: replace `0 20 * * * cron_daily_refresh.sh` with
  `@reboot` supervisor + `*/2 * * * * cron_liveness_check.sh`.
- 1 integration test added (auto-recovery against stubbed RealClient).

## Non-Goals
- Replacing `populate_expected_bars.py` (call it once per day at startup).
- Tinkoff 30s timeout for sparse bonds (ADAPT-11, separate
  priority-queue-redesign change).
- New data sources (CBR/FRED) — separate `ru-data-sources` change.
- ML model training integration.
- Mobile UI changes.
