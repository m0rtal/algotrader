# Design: Coordinated Market-Data Write Sections

## Scope Boundary

This capability coordinates only the concurrent market-data mutations enumerated in `proposal.md`. It does not promise global serialization of heartbeat, pipeline bookkeeping, guardian, corporate-action, dividend, maintenance, universe, or API-startup writes. SQLite WAL, transactions, and `busy_timeout` remain their current protection.

## Shared Lock Namespace

`writer_lock_path(db_path)` returns `Path(f"{db_path}.writer.lock")`. The implementation opens this path with exclusive-create-compatible flags plus `O_NOFOLLOW` where supported, and accepts only `LOCK_EX`; `LOCK_SH` is not part of the API. The basename is checked against the platform name-length limit before opening; an invalid/overlong path fails explicitly before any mutation.

The persistent lock file contains no correctness state. Role, phase, PID, timestamps, and file age are diagnostics only. Ownership is determined solely by the kernel lock on the open descriptor. A dead recorded PID or old file never permits bypass.

Tests use file-backed temporary databases, never `:memory:` or production paths.

## Lock API

The context manager accepts `db_path`, `role`, `phase`, and `timeout_seconds`; default timeout is 30 seconds, matching SQLite's configured busy timeout. It uses `fcntl.flock(fd, LOCK_EX | LOCK_NB)` with monotonic bounded retry.

A process-local guard keyed by canonical lock path rejects nested acquisition before opening a second descriptor. The public writer acquires the lock; its private transaction helper assumes ownership and never reacquires. The context manager always calls `LOCK_UN` and closes the descriptor in `finally`.

No caller may fork, spawn a subprocess, or pass the lock descriptor while the context is held. This prevents inherited descriptors from extending ownership after the parent leaves the section.

Acquisition timeout raises `WriterLockBusy` with non-secret diagnostics. Path/open/configuration errors raise `WriterLockError`. Neither exception permits a write fallback.

## Transaction Boundaries

### Common bar writer

Candle normalization and upstream fetch happen before acquisition. `replace_bars_for_figi` then acquires once and calls a private transaction helper covering:

1. `BEGIN IMMEDIATE`;
2. optional per-FIGI delete;
3. bar insert;
4. `instrument_metadata` aggregate update;
5. commit or rollback.

The lock is released before any UI snapshot calculation.

The raw `bars` INSERT in `_async_backfill_impl` is routed through this common writer or an equivalent private coordinated transaction. No in-scope bar insertion may bypass the namespace.

### Evidence reconciliation

`reconcile_no_trade_evidence` mutates SQLite and commits internally today. It therefore runs only after the bar lock has been released, then acquires the same lock as a second, non-nested write-section around its DELETE and commit/rollback. Failure to acquire is logged as best-effort deferral; the next bar/evidence cycle retries reconciliation. It never rolls back an already-committed real bar.

`record_no_trade_evidence` likewise acquires the shared lock around its INSERT/UPDATE and internal commit when called as a public writer. A private transaction helper is used if a future caller already owns the lock; nested public acquisition remains forbidden.

### Historical evidence script

Reads, MOEX probes, fetches, and sleeps are unlocked. For each FIGI:

- `listed_till` UPDATE is one coordinated transaction;
- evidence persistence is a separate coordinated transaction;
- neither lock spans the upstream request or sleep.

### Expected-bars script

The script reads and computes all expected values without the shared lock. It then acquires once, starts `BEGIN IMMEDIATE`, applies the complete UPDATE batch, and commits or rolls back atomically. Direct invocation and cron invocation use the same Python lock. The shell `.expected-bars.lock` remains a separate outer guard against duplicate wrapper processes and is not a substitute for the shared lock.

## Contention and Exit Codes

On shared-lock timeout, an auxiliary CLI prints one structured deferral line, performs no pending mutation, and returns `75` (`EX_TEMPFAIL`). The next cron tick retries naturally.

`cron_expected_bars.sh` handles rc=75 separately: log `DEFER writer-lock-busy`, do not run the SQLite-busy retry loop, and exit 0. Its current bounded retry remains only for SQLite `database is locked` failures from non-participating legacy writers. Any other non-zero child status remains a job failure.

Library callers receive `WriterLockBusy`. Critical daily-chain callers translate it into their existing failed phase result so supervisor/recovery logic sees failure; they do not report successful data work.

## Dry-Run Contract

Only scripts that already expose `--dry-run` are covered: foreign bars, same-day catch-up, and no-trade evidence. Their dry-run paths perform no SQLite mutation and never acquire the shared writer lock. `populate_expected_bars.py` has no dry-run mode and is not named by this contract.

## Diagnostics

A deferral/error record includes bounded values for:

- role: `bar-writer`, `foreign-bars`, `same-day`, `no-trade-evidence`, `expected-bars`, or `evidence-reconcile`;
- phase: `bars`, `listed-till`, `evidence`, `expected-bars`, or `reconcile`;
- PID;
- database and lock paths;
- timeout;
- reason and result (`deferred` or `error`).

No token, credential, connection string, request payload, or broker response is logged. Successful per-FIGI acquisitions are not logged in production; contention tests provide the concurrency proof.

## Verification

1. Unit tests: path derivation, path-length failure, symlink rejection where supported, exclusive mode, normal/exception release, independent databases, diagnostics, Python-level non-reentrancy, and fork/spawn prohibition contract.
2. Two-subprocess test: a shared counter/file proves overlap peak is exactly one; a timeout proves the losing process performs no mutation.
3. Boundary tests: another process can acquire the lock during simulated network fetch, calculation, sleep, and between FIGIs.
4. Integration tests: common bars, raw bar path, `listed_till`, evidence record/reconcile, expected-bars batch, and each existing dry-run.
5. Exit tests: each auxiliary CLI returns 75 on contention; expected-bars wrapper translates only 75 to logged deferral/exit 0.
6. Regression suites: MOEX identity, evidence, expected bars, backfill, same-day, isolated watchdog, and SQLite integrity.
7. Production proof after merge: backup first; run actual scheduled entrypoints; inspect explicit result/deferral records; verify one expected supervisor tree per configured slot, fresh heartbeat, no `database is locked` storm, sane row counts, and `PRAGMA quick_check=ok`. `quick_check` proves storage integrity only; subprocess contention tests and result logs prove coordination.
8. ML readiness is measured separately with the existing gate. This capability does not claim coverage is already at least 95%.

## Risks

- Overbroad lock scope starves short jobs: boundary tests enforce unlocked fetch/compute/sleep.
- Hidden market-data bypass preserves races: writer inventory plus raw-bar regression test fail closed.
- Nested helper deadlocks: process-local guard and public/private API split.
- Fork inheritance prolongs ownership: spawning while held is forbidden and tested at the helper boundary.
- Legacy writers still contend: SQLite transactions and busy timeout remain defense in depth; no shared-lock timeout bypass is allowed.