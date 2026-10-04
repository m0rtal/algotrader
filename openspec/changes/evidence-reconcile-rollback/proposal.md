# Evidence reconciliation commit-failure rollback

## Why

`reconcile_no_trade_evidence` commits outside its existing protected transaction body. A failed commit can leave the borrowed cached connection in a write transaction after the shared flock releases. The bar writer's best-effort hook can swallow that ordinary SQLite exception. This is a source-confirmed defect; the actual production trigger has not been proven.

## What Changes

- Put reconciliation DELETE and commit in one exception-protected section under the existing `evidence-reconcile` / `reconcile` writer lock.
- Attempt rollback on `BaseException` before unlock, preserving the original failure if rollback also fails.
- Reuse PR #186's numeric `is_sqlite_busy` classifier. Translate primary and extended SQLite BUSY into `WriterLockBusy(reason="sqlite-busy")` after rollback.
- Leave borrowed connections open, committed bars intact, and caller best-effort policy unchanged.

## Impact

Only `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`, temporary-database regression tests, and writer-coordination documentation change. No public signature, schema, dependency, timeout, retry, or sleep changes.

## Non-Goals

Do not claim this defect caused an observed production incident. Do not expand writer coordination to universe, corporate-action, dividend, heartbeat, guardian, maintenance, startup-repair, circuit-breaker, or other excluded bookkeeping paths. No network or production operations belong to this implementation task; the parent owns review, full isolated regression, PR, merge, and deployment.
