# Writer Coordination Specification (delta)

## ADDED Requirements

### Requirement: Atomic Evidence Reconciliation Failure Cleanup

The evidence reconciliation wrapper SHALL protect its DELETE and commit together under its existing shared writer lock, attempt rollback on any `BaseException` before unlock, and leave its borrowed connection open. It SHALL translate only `sqlite3.OperationalError` with numeric primary code `SQLITE_BUSY`, including extended BUSY codes, into `WriterLockBusy` with role `evidence-reconcile`, phase `reconcile`, and reason `sqlite-busy`. Other errors SHALL propagate unchanged from the wrapper. If rollback also fails, the original error SHALL remain authoritative and lock release SHALL still be attempted. Existing timeouts and caller policies SHALL remain unchanged without new retries or sleeps.

#### Scenario: Reconciliation commit fails after DELETE

- GIVEN a borrowed cached connection to a file-backed database and separately committed real bars
- WHEN the real reconciliation DELETE executes and its commit fails
- THEN rollback is attempted while the actual kernel flock remains held
- AND successful rollback closes the SQLite transaction before unlock and restores deleted evidence
- AND the connection remains open and reusable without rolling back the committed bars

#### Scenario: Numeric SQLite BUSY is deferred

- GIVEN reconciliation owns its shared writer lock
- WHEN DELETE or commit raises SQLite BUSY code 5 or extended BUSY code 517
- THEN rollback is attempted before unlock
- AND the wrapper raises `WriterLockBusy` chaining that exact SQLite exception
- AND the bar writer logs one bounded `sqlite-busy` deferral without losing its committed bars

#### Scenario: Non-BUSY and cancellation failures retain meaning

- GIVEN reconciliation owns its shared writer lock and borrows its connection
- WHEN DELETE or commit raises another SQLite error, an error without numeric code, or a direct `BaseException`
- THEN rollback is attempted before unlock
- AND the wrapper re-raises the original error rather than labelling it BUSY
- AND the bar writer's existing policy for ordinary hook errors remains unchanged

#### Scenario: Rollback itself fails

- GIVEN reconciliation DELETE or commit has failed
- WHEN the rollback attempt also raises
- THEN the original failure is preserved, with BUSY conversion only when that original failure qualifies
- AND the shared writer lock is released and the borrowed connection is not closed
