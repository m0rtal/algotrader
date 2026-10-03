# Proposal: Coordinate Concurrent Market-Data Writers

## Why

Production runs independent jobs that mutate market-data state in one SQLite database: the supervised daily MOEX/Tinkoff worker, foreign-bar catch-up, same-day MOEX catch-up, no-trade evidence backfill, and expected-bars refresh. Their schedules overlap. WAL and `busy_timeout` preserve SQLite consistency, but do not prevent partial jobs, repeated contention, or manual cron/process coordination.

This change establishes one fail-closed advisory lock for the market-data mutations these jobs can perform concurrently. The lock protects only SQLite write-sections. It never covers network fetches, calculations, sleeps, or a supervisor/process lifetime.

## In-Scope Writers

1. `replace_bars_for_figi` and every daily, MOEX, Tinkoff, foreign, same-day, recovery, and seed caller that reaches it.
2. The raw `bars` INSERT in `backfill.py::_async_backfill_impl`; it SHALL be routed through the coordinated common bar writer or an equivalent coordinated transaction before this change is accepted.
3. `record_no_trade_evidence` and `reconcile_no_trade_evidence`.
4. `backfill_no_trade_evidence.py` mutations to `instruments.listed_till` and `moex_no_trade_evidence`.
5. `populate_expected_bars.py` mutations to `instruments.expected_bars`.
6. The existing `cron_expected_bars.sh` wrapper, which retains its separate `.expected-bars.lock` solely to reject duplicate wrapper invocations while the Python writer participates in the shared market-data lock.

## What Changes

1. Add a stdlib-only `fcntl.flock` helper using `<database-path>.writer.lock`, `LOCK_EX | LOCK_NB`, monotonic bounded retry, and kernel file-descriptor ownership.
2. Add an explicit Python-level non-reentrancy guard. Public writer functions acquire once; private transaction helpers never reacquire.
3. Acquire immediately before `BEGIN IMMEDIATE` or the first mutating SQL statement; release immediately after commit or rollback.
4. Keep the bar transaction and post-commit evidence reconciliation as two separate, non-nested coordinated write-sections.
5. Make expected-bars one explicit `BEGIN IMMEDIATE` batch under the shared lock. Its read and calculation phase remains unlocked.
6. Make each `listed_till` update and each evidence persistence/reconciliation transaction use the same shared lock without holding it during MOEX requests or sleeps.
7. Return exit code `75` (`EX_TEMPFAIL`) when an auxiliary CLI defers because the shared lock remained busy. No timeout authorizes an uncoordinated write.
8. Make `cron_expected_bars.sh` log rc=75 as a deferral, skip its SQLite-busy retry branch for that result, and exit 0 so the next hourly schedule retries normally. Other failures remain non-zero.
9. Preserve existing identity validation, transactions, and SQLite busy handling as defense in depth.
10. Add isolated subprocess contention tests and per-entrypoint integration tests.

## Explicitly Out of Scope

The shared market-data lock does not claim to serialize every SQLite write. These existing bookkeeping or separate-domain paths retain their current transaction contracts:

- `pipeline`, `pipeline_log`, `pipeline_runs`, and heartbeat writes;
- guardian sentinel locking;
- corporate actions, dividends, and forward-adjusted derived tables;
- API startup repair and maintenance/cleanup commands;
- circuit-breaker and completeness bookkeeping in `instrument_metadata`;
- universe discovery/upsert outside an in-scope bar transaction;
- UI snapshot file generation;
- alerting, UI, SLO storage, process killing, signals, priority queues, daemons, Redis, Postgres, or an IPC broker.

These exclusions are not evidence that those writers can never contend. They define this change's narrower guarantee: scheduled market-data bar/evidence/denominator jobs do not overlap their SQLite mutation sections. A later change may coordinate other domains after their transaction and liveness requirements are specified.

## Non-Goals

- No process-lifetime supervisor lock.
- No lock during MOEX/Tinkoff requests, calculations, sleeps, or dry-runs.
- No stale-file, timestamp, or recorded-PID bypass.
- No ML gate formula change, fabricated bars, or asset-class exclusion.
- No `--dry-run` addition to `populate_expected_bars.py`.
- No production deployment before reviewed PR merge, backup, and production-shaped verification.

## Baseline Note

Two existing `test_worker_daily_chain` mocks reject the already-deployed `recent_tail_days=5` keyword. The implementation SHALL update those test doubles to accept and assert the existing argument without changing the production call site; this is test-harness repair, not new behavior.