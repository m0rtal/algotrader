# Market-Data Writer Coordination Specification

## Purpose

Define fail-closed serialization for concurrent market-data SQLite mutation sections without holding locks across network I/O, calculations, sleeps, or process lifetimes.

## Requirements

### Requirement: Shared Market-Data Lock Namespace

The system SHALL derive one exclusive advisory lock path as `<database-path>.writer.lock` for all in-scope market-data mutations: coordinated bar writes including `_async_backfill_impl`, evidence record/reconcile, `listed_till`, and expected-bars updates.

#### Scenario: Same database serializes market-data write-sections

- GIVEN independent processes target the same file-backed SQLite database
- WHEN they enter in-scope market-data write-sections
- THEN they use the same `LOCK_EX` writer-lock path
- AND measured peak simultaneous critical-section occupancy is exactly one

#### Scenario: Different databases remain independent

- GIVEN independent processes target different canonical SQLite paths
- WHEN they enter coordinated write-sections
- THEN their writer-lock paths differ
- AND neither blocks the other

### Requirement: Safe Lock Path

The system SHALL validate and open the writer-lock path without following a pre-existing symlink where the platform supports that protection, and SHALL fail before mutation if the lock basename exceeds the platform limit.

#### Scenario: Unsafe lock path is rejected

- GIVEN the derived lock path is overlong or resolves through a forbidden symlink
- WHEN a writer attempts acquisition
- THEN acquisition fails explicitly before any SQLite mutation
- AND no fallback lock path is silently selected

### Requirement: Fail-Closed Bounded Acquisition

The system SHALL use monotonic bounded acquisition with a default timeout of 30 seconds and SHALL perform no pending mutation after timeout.

#### Scenario: Auxiliary writer times out

- GIVEN another process holds the shared writer lock
- WHEN an auxiliary market-data writer reaches its mutation and times out
- THEN it performs no pending SQLite mutation
- AND it emits bounded non-secret deferral diagnostics
- AND its CLI exits 75
- AND it does not attempt an uncoordinated write

#### Scenario: Diagnostic metadata appears stale

- GIVEN the lock file is old or its recorded PID is absent
- WHEN the kernel lock remains held
- THEN the caller continues to treat the lock as held
- AND file age, timestamp, or PID never authorizes bypass

### Requirement: Write-Section-Only Scope

The system SHALL acquire immediately before `BEGIN IMMEDIATE` or the first mutating statement and SHALL release immediately after commit or rollback.

#### Scenario: Network and computation remain unlocked

- GIVEN a writer performs MOEX/Tinkoff requests, calculations, or sleeps before mutation
- WHEN those operations are in progress
- THEN that writer does not hold the shared writer lock
- AND another process can acquire it

#### Scenario: Each FIGI releases before the next fetch

- GIVEN a loop calls a public per-FIGI writer
- WHEN one invocation commits or rolls back
- THEN that invocation releases the lock before the loop fetches, calculates, or sleeps for the next FIGI

### Requirement: Exception-Safe Non-Reentrant Ownership

The system SHALL enforce non-reentrancy in Python, unlock and close in `finally`, and prohibit process creation while the lock is held.

#### Scenario: Public writer delegates to private transaction helper

- GIVEN a public writer has acquired the shared lock
- WHEN it calls its private transaction helper
- THEN the helper does not reacquire the lock
- AND exactly one acquisition protects that transaction

#### Scenario: Body raises

- GIVEN a writer owns the lock and its transaction body raises
- WHEN rollback completes or also raises
- THEN unlock and descriptor close are still attempted
- AND a later process can acquire the lock

#### Scenario: Process creation is attempted while held

- GIVEN the current execution context owns the writer lock
- WHEN code attempts to fork, spawn, or transfer the descriptor inside the critical section
- THEN the helper contract rejects that operation in tests and production code contains no such call path

### Requirement: Coordinated Bar Mutations

The system SHALL coordinate all in-scope bar insertions, including the common `replace_bars_for_figi` path and `_async_backfill_impl` raw insertion path, without weakening identity validation.

#### Scenario: Common bar write commits atomically

- GIVEN a verified candle batch reaches `replace_bars_for_figi`
- WHEN the bar transaction begins
- THEN the writer lock is already held
- AND bar mutation plus `instrument_metadata` aggregate update commit or roll back together

#### Scenario: Raw bar path cannot bypass coordination

- GIVEN `_async_backfill_impl` produces bars
- WHEN it persists them
- THEN it delegates to the coordinated common writer or an equivalent coordinated private transaction
- AND no raw uncoordinated `INSERT INTO bars` remains on that path

#### Scenario: MOEX identity mismatch remains rejected

- GIVEN the writer can acquire the lock
- AND a row fails SECID, BOARDID, or ISIN verification
- WHEN the path evaluates that row
- THEN no bar is inserted
- AND coordination does not bypass the existing identity guard

### Requirement: Separate Evidence Transactions

The system SHALL coordinate evidence record and reconciliation as explicit, non-nested transactions separate from the committed bar transaction.

#### Scenario: Real bar commits before reconciliation

- GIVEN a real bar transaction commits successfully
- WHEN post-commit reconciliation starts
- THEN the bar lock has already been released
- AND reconciliation reacquires the same namespace for its DELETE and commit or rollback

#### Scenario: Reconciliation defers

- GIVEN the real bar is committed and reconciliation cannot acquire the lock
- WHEN its timeout expires
- THEN the real bar remains committed
- AND no evidence DELETE occurs in that attempt
- AND the deferral is logged for a later cycle

#### Scenario: Historical evidence has two write-sections

- GIVEN no-trade evidence processing discovers a `listed_till` update and evidence rows for one FIGI
- WHEN it persists them
- THEN the `listed_till` transaction and evidence transaction each acquire and release the shared lock separately
- AND neither lock covers MOEX fetch or sleep

### Requirement: Atomic Expected-Bars Batch

The system SHALL compute expected-bar values without the shared lock and SHALL apply the complete update batch in one coordinated `BEGIN IMMEDIATE` transaction.

#### Scenario: Direct invocation is coordinated

- GIVEN `populate_expected_bars.py` is invoked directly or by its shell wrapper
- WHEN it starts mutating `instruments.expected_bars`
- THEN it owns `<database-path>.writer.lock`
- AND all expected-bars updates commit or roll back as one batch

#### Scenario: Wrapper receives temporary failure

- GIVEN the Python expected-bars writer exits 75 because the shared lock is busy
- WHEN `cron_expected_bars.sh` handles the result
- THEN it logs `DEFER writer-lock-busy`
- AND it does not enter the SQLite-busy retry loop
- AND the wrapper exits 0 so the next schedule retries normally

### Requirement: Existing Dry-Runs Stay Read-Only

The existing `--dry-run` modes of foreign bars, same-day catch-up, and no-trade evidence SHALL not acquire the shared writer lock or mutate SQLite.

#### Scenario: Existing auxiliary dry-run executes

- GIVEN one of those three scripts is invoked with `--dry-run`
- WHEN it fetches and evaluates candidates
- THEN it performs no SQLite mutation
- AND it does not acquire the shared writer lock
- AND relevant before/after counters remain equal

### Requirement: Bounded Coordination Diagnostics

The system SHALL report contention with bounded role, phase, PID, database path, lock path, timeout, reason, and result fields, and SHALL not log successful per-FIGI acquisitions in production.

#### Scenario: Deferral is recorded safely

- GIVEN acquisition times out
- WHEN the caller records the outcome
- THEN role is one of `bar-writer`, `foreign-bars`, `same-day`, `no-trade-evidence`, `expected-bars`, or `evidence-reconcile`
- AND phase is one of `bars`, `listed-till`, `evidence`, `expected-bars`, or `reconcile`
- AND the record contains no API key, token, credential, connection string, request payload, or upstream response

### Requirement: Explicit Capability Boundary

The system SHALL describe this guarantee as serialization of in-scope market-data mutation sections, not serialization of every SQLite write.

#### Scenario: Out-of-scope bookkeeping writes remain unchanged

- GIVEN pipeline, heartbeat, guardian, corporate-action, dividend, maintenance, universe, startup-repair, or circuit-breaker bookkeeping executes
- WHEN this capability is deployed
- THEN those paths retain their existing transaction behavior
- AND no acceptance report claims they were serialized by this market-data lock
