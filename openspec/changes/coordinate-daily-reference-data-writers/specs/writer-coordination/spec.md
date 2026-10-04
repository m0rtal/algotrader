# Writer Coordination Specification (delta)

## MODIFIED Requirements

### Requirement: Shared Market-Data Lock Namespace

The system SHALL derive one exclusive advisory lock path as `<database-path>.writer.lock` for all in-scope market-data mutations: coordinated bar writes including `_async_backfill_impl`, evidence record/reconcile, `listed_till`, expected-bars updates, and the daily reference-data transaction owners `universe.upsert_instruments`, `BackfillRunner._upsert_instrument`, `BackfillRunner._seed_metadata_for_figi`, `BackfillRunner._upsert_metadata`, `merge_into_corporate_actions`, the adjustment transaction in `worker._step_corporate_actions`, and `merge_into_dividends`.

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

### Requirement: Bounded Coordination Diagnostics

The system SHALL report contention with bounded role, phase, PID, database path, lock path, timeout, reason, and result fields, and SHALL not log successful per-FIGI acquisitions in production.

#### Scenario: Deferral is recorded safely

- GIVEN acquisition times out
- WHEN the caller records the outcome
- THEN role is one of `bar-writer`, `foreign-bars`, `same-day`, `no-trade-evidence`, `expected-bars`, `evidence-reconcile`, `universe-sync`, `backfill-metadata`, `corporate-actions`, or `dividends`
- AND phase is one of `bars`, `listed-till`, `evidence`, `expected-bars`, `reconcile`, `instruments`, `metadata`, `corporate-actions`, `adjusted-bars`, or `dividends`
- AND the record contains no API key, token, credential, connection string, request payload, or upstream response

### Requirement: Explicit Capability Boundary

The system SHALL describe this guarantee as serialization of in-scope market-data mutation sections, not serialization of every SQLite write.

#### Scenario: Out-of-scope bookkeeping writes remain unchanged

- GIVEN pipeline, heartbeat, guardian, maintenance, startup-repair, circuit-breaker, ingestion-log, dividend-throttle-queue bookkeeping, or the legacy curated corporate-action importer executes outside the explicit daily reference-data transaction inventory
- WHEN this capability is deployed
- THEN those paths retain their existing transaction behavior
- AND no acceptance report claims they were serialized by this market-data lock

## ADDED Requirements

### Requirement: Daily Reference-Data Transaction Ownership

The system SHALL coordinate exactly the additional daily transaction owners named in Shared Market-Data Lock Namespace, SHALL preserve their current row/list transaction granularity and data semantics, and SHALL keep preparatory reads, row normalization, split derivation, network requests, limiter waits, and sleeps outside the shared lock. Each owner SHALL begin its transaction inside the lock, keep every mutation and commit or attempted rollback inside it, and never close a borrowed connection. Transaction-local consistency checks and SQL mutation calculations SHALL remain inside the protected transaction. No whole discovery, fetch, run, phase, or process wrapper SHALL acquire this lock.

#### Scenario: Universe preserves existing per-row commit and local fields

- GIVEN broker rows have been fetched, filtered, and normalized outside the lock
- WHEN `universe.upsert_instruments` persists each row
- THEN each existing per-row transaction acquires and releases the shared lock independently
- AND the writer owns its connection rather than sharing the cached telemetry connection
- AND figi-keyed UPSERT, duplicate-ticker support, local coverage fields, and returned row count retain their existing meaning
- AND the 100-row slicing loop does not become a new atomic-batch contract

#### Scenario: Backfill metadata has explicit bounded owners

- GIVEN the runner has prepared an instrument or metadata update without holding the shared lock
- WHEN `_upsert_instrument`, `_seed_metadata_for_figi`, or `_upsert_metadata` persists it
- THEN that helper owns exactly one acquisition for its current transaction
- AND its existing fields and conflict semantics remain unchanged
- AND BackfillRunner logging, circuit-breaker bookkeeping, and whole-run orchestration do not acquire this lock

#### Scenario: Corporate derivation and adjustment are separate transactions

- GIVEN `derive_splits.run_derivation` has computed candidates outside the lock
- WHEN `merge_into_corporate_actions` persists them and the worker subsequently adjusts bars
- THEN the merge transaction commits or rolls back and releases before the adjustment acquisition
- AND the worker prepares chronological event tuples outside the adjustment lock
- AND one worker-owned transaction applies those tuples through the existing `apply_forward_split` borrower and commits or rolls back before unlock
- AND the borrower does not acquire, commit, roll back, or close the connection
- AND the current duplicate-skip, chronological, forward-adjustment, and return-count behavior is preserved

#### Scenario: Dividend fetch and retry queue are not lock owners

- GIVEN a dividend FIGI passes through its limiter, fetch, and row mapping
- WHEN `merge_into_dividends` persists its prepared rows
- THEN only the existing dividend row-list transaction acquires the shared lock
- AND duplicate PKs, explicit retrospective `DividendRow(revision_n=2)` merge inputs, and actual insert count retain their current meaning
- AND the existing fetch mapper retains `revision_n=1`; a fetch smoke does not claim to generate revision 2
- AND fetch, mapping, queue, dequeue, and rate-limit waits do not hold or acquire this lock

#### Scenario: Contention leaves pending work retryable

- GIVEN an additional daily owner cannot acquire flock or encounters numeric primary SQLite BUSY after acquiring it
- WHEN the attempt fails
- THEN no pending transaction is committed and rollback is attempted before unlock for any started transaction
- AND only numeric primary SQLite BUSY is translated to `WriterLockBusy` with `reason=sqlite-busy` and the original error chained
- AND other failures and interruptions retain their original meaning
- AND the daily phase reports failure or deferral rather than successful completion, while existing auxiliary CLI adapters exit 75 with the shared bounded formatter
- AND previously committed rows or separate bar/derivation transactions remain committed for the next normal retry
- AND no added retry, sleep, timeout increase, fallback write, or failed-work success checkpoint is introduced

#### Scenario: Rollback failure does not prevent owned connection close

- GIVEN an owner has an active transaction and its primary failure is followed by an injected rollback failure
- WHEN cleanup runs
- THEN rollback is attempted under flock, unlock and native owned-connection close still run, and the primary exception remains the reported failure
- AND a later independent connection sees no partial pending rows and can acquire the lock
- AND the test observer allows active-at-close only for a per-connection flag set by the injected rollback failure
- AND separate healthy commit/rollback cases require idle before close, with native close in `finally` even if the observer assertion fails
- AND no borrowed connection is closed and no test requires reuse of the failed handle

#### Scenario: Metadata BUSY reaches the real worker error boundary

- GIVEN a newly coordinated BackfillRunner helper raises `WriterLockBusy` with `role=backfill-metadata` during discovery or a per-ticker metadata update
- WHEN actual `BackfillRunner.run` is driven by `worker.run_worker` in scheduled or manual mode
- THEN the runner emits final `done.status=error`, resets IDLE, and propagates the original exception rather than returning normally or reporting `done.status=ok`
- AND already-started per-ticker jobs settle before the worker closes its client
- AND the worker returns rc=2 and records the exact pipeline phase as `status=err` with the bounded formatter, never pipeline `ok` or rc=0
- AND actual historical or trailing gap recovery propagates this metadata failure to `(False, format_busy_defer(exc))`, not successful zero-bar completion
- AND the trailing call uses the existing `_backfill_one(..., to=...)` signature and executes the real metadata owner
- AND earlier committed rows remain committed and the next normal retry can reuse them
- AND other BUSY roles and ordinary failures retain their existing caller policies, without a whole-run lock or a generic success-policy repair

#### Scenario: Same-process contender cannot create a second critical section

- GIVEN a thread already has an active acquisition for a canonical database path
- WHEN another context in the same Python process attempts that path, including through an alias or another new diagnostic role
- THEN the existing process-local guard raises `WriterLockReentrant` before that contender mutates
- AND measured peak simultaneous in-scope critical-section occupancy in that process is exactly one
- AND separate independent-process tests exercise actual first-side and derived-side owners without changing their kernel-lock semantics

#### Scenario: Explicit inventory replaces only approved domain exclusions

- GIVEN the previous static inventory forbids writer-lock imports in universe and dividend modules
- WHEN this approved expansion is implemented
- THEN those blanket exclusions are replaced with exact role/phase-qualified owner proofs and explicit no-acquisition orchestration and queue proofs
- AND original bar, evidence, raw-path, no-process-creation, identity, dry-run, and reconciliation tests remain enforced
- AND pipeline, heartbeat, guardian, maintenance, legacy importer, startup-repair, circuit-breaker and SQLite connection/query helper exclusions remain explicit
- AND no global `get_connection`, `execute`, or `execute_returning_id` lock or claim of eliminating all SQLite BUSY is introduced

#### Scenario: Standing-goal acceptance requires full autonomous daily evidence

- GIVEN capability tests and bounded fixture or production smoke have passed but standing-goal acceptance is still open
- WHEN production acceptance is assessed after an operator-approved deployment
- THEN one full scheduled daily cycle covers every first/derived phase, including bonds depth, freshness check and guardian
- AND seven consecutive days of complete autonomous daily cycles demonstrate natural retry/resume without manual restart, pause or forced backfill
- AND at each qualifying daily observation ML-ready coverage is at least 95% and freshness at most 4 hours for every required cohort, not merely a passing global average
- AND measurement uses the complete canonical required FIGI population, unchanged positive cached `instruments.expected_bars` denominator and existing session/evidence/delisting rules
- AND unknown/nonpositive denominators and failed cohorts are reported rather than counted as ready or excluded
- AND timestamped per-phase and per-cohort evidence, committed-row continuity and actual retry outcomes are retained; missing evidence or an interrupted day leaves this gate open
- AND two observations, lock-test coverage or a now-passing bounded smoke cannot replace the seven-day criterion or prove that this expansion caused the smoke result
