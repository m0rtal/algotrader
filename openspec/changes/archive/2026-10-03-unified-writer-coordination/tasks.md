# Tasks

## 1. Baseline and Writer Inventory

- [ ] Record base SHA `6b9b23ed3bbfe739c638ec3fff3b24bcebefd604` and run the safe focused baseline suites with `env -u PYTHONPATH -u PYTHONHOME .venv/bin/python -m pytest tests/test_cron_expected_bars.py tests/test_sqlite_busy_timeout.py tests/test_worker_daily_chain.py -q`.
- [ ] Update only the two stale `test_worker_daily_chain` side effects to accept and assert deployed `recent_tail_days=5`; do not change the production call.
- [ ] Create a checked writer-inventory test or table covering every in-scope path from `proposal.md`, including `_async_backfill_impl` and both evidence transactions.

## 2. Shared Lock Helper — TDD

- [ ] RED: tests for deterministic `<db>.writer.lock`, canonical-path separation, platform name-length failure, exclusive-only mode, and symlink rejection where supported.
- [ ] RED: two-subprocess test proving peak simultaneous critical-section occupancy is exactly one.
- [ ] RED: normal release, exception release, timeout/no-mutation, independent database paths, diagnostics, process-local nested-acquisition rejection, and fork/spawn prohibition tests.
- [ ] GREEN: implement minimal stdlib helper in `ingestion/writer_lock.py` with 30-second default, monotonic retry, `LOCK_UN`+close in `finally`, and dedicated `WriterLockBusy`/`WriterLockError` exceptions.
- [ ] Run `tests/test_writer_lock.py` and `git diff --check`.

## 3. Common and Raw Bar Writers — TDD

- [ ] RED: `replace_bars_for_figi` performs no bars/metadata mutation after lock timeout and releases after rollback.
- [ ] RED: candle normalization and simulated fetch occur while another process can acquire the shared lock.
- [ ] GREEN: split public acquisition from private bar transaction; cover only `BEGIN IMMEDIATE` through commit/rollback.
- [ ] RED/GREEN: route `_async_backfill_impl` raw `INSERT INTO bars` through the common coordinated writer or an equivalent coordinated private transaction.
- [ ] Run bar writer, backfill, source attribution, identity, and SQLite timeout tests.

## 4. Evidence Transactions — TDD

- [ ] RED: post-bar `reconcile_no_trade_evidence` reacquires the lock as a separate transaction after the bar lock is released.
- [ ] RED: reconciliation timeout leaves the real bar committed, records deferral, and performs no DELETE.
- [ ] RED: public `record_no_trade_evidence` acquires exactly once; private transaction helper does not reacquire.
- [ ] RED: `backfill_no_trade_evidence.py` coordinates `listed_till` and evidence as separate per-FIGI write-sections while fetch/sleep stays unlocked.
- [ ] GREEN: implement the two non-nested evidence transaction boundaries and preserve real-bar-wins semantics.
- [ ] Run no-trade evidence, expected denominator, identity, and reconciliation tests.

## 5. Expected-Bars Batch — TDD

- [ ] RED: direct `populate_expected_bars.py` invocation acquires `<db>.writer.lock`, starts `BEGIN IMMEDIATE`, and updates all rows atomically.
- [ ] RED: timeout produces no `expected_bars` mutation and exits 75.
- [ ] RED: calculation occurs without lock; mutation batch occurs with lock.
- [ ] RED: `cron_expected_bars.sh` maps child rc=75 to `DEFER writer-lock-busy` and exit 0 without SQLite-busy retry; other failures remain non-zero.
- [ ] GREEN: implement Python batch coordination and preserve the shell `.expected-bars.lock` only as duplicate-wrapper protection.
- [ ] Run `tests/test_cron_expected_bars.py` plus new expected-bars lock integration tests.

## 6. Auxiliary CLI Outcomes and Dry-Runs

- [ ] RED: foreign-bars, same-day, and no-trade-evidence CLIs return 75 with role/phase/PID/db/lock/timeout/reason on shared-lock contention.
- [ ] RED: those three existing `--dry-run` modes do not acquire the lock and leave bars, evidence, `listed_till`, and expected-bars counters unchanged as applicable.
- [ ] RED: diagnostics contain no token, credential, connection string, request payload, or upstream response.
- [ ] GREEN: translate `WriterLockBusy` consistently without uncoordinated fallback or false success.

## 7. Spec and Branch Verification

- [ ] Run `openspec validate unified-writer-coordination --strict`.
- [ ] Run all new lock/contention tests and every touched writer integration test with `PYTHONPATH` and `PYTHONHOME` unset.
- [ ] Run existing MOEX identity, no-trade evidence, expected-bars, backfill, same-day, isolated watchdog, and SQLite integrity suites.
- [ ] Compare the identical safe scope on base and head; report all pre-existing failures separately.
- [ ] Run `bash -n` on changed shell files and `git diff --check`.
- [ ] Obtain independent concurrency and data-integrity review of the exact branch SHA; fix all Critical/Important findings and re-review.

## 8. PR and Controlled Deployment

- [ ] Push `fix/unified-writer-coordination`, open PR, verify remote head and actual test checks, then merge only after independent approval.
- [ ] Create restricted online SQLite backup via `sqlite3.Connection.backup()`, verify `PRAGMA integrity_check`, and back up/read back crontab before deployment.
- [ ] Deploy only the reviewed merge commit; preserve untracked operational files and keep developer/QA code cron disabled.
- [ ] Run production-shaped smoke through actual scheduled market-data entrypoints; verify explicit results/deferrals, configured supervisor topology, fresh heartbeat, no SQLite lock storm, sane counts, and `PRAGMA quick_check=ok`.
- [ ] Observe multiple real cron intervals without manual process/cron coordination.
- [ ] Measure existing ML readiness gate separately; do not claim this coordination change alone reaches 95%.