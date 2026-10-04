# Writer Coordination Specification (delta)

## ADDED Requirements

### Requirement: Narrow SQLite Evidence BUSY Deferral

The listed-till CLI write and evidence record write SHALL roll back failed mutations or commits before releasing the shared flock, SHALL preserve borrowed connections, and SHALL translate only sqlite3.OperationalError with numeric primary error code SQLITE_BUSY into WriterLockBusy with reason sqlite-busy. Existing bounded waits SHALL remain unchanged without additional retry or sleep.

#### Scenario: Independent SQLite writer blocks listed-till
- GIVEN another connection holds BEGIN IMMEDIATE without the advisory flock
- WHEN the listed-till write acquires flock and encounters SQLite BUSY
- THEN rollback is attempted before unlock
- AND the CLI emits one bounded deferral with phase listed-till and reason sqlite-busy and exits 75
- AND no pending listed-till mutation commits

#### Scenario: Evidence write or commit encounters BUSY
- GIVEN a borrowed file-backed connection and acquired evidence flock
- WHEN mutation or commit raises SQLITE_BUSY or an extended BUSY code
- THEN rollback occurs before unlock and WriterLockBusy chains the original error
- AND no partial evidence remains and the borrowed connection remains open

#### Scenario: Other failures retain their meaning
- GIVEN an evidence or listed-till mutation or commit fails
- WHEN the error is not numeric SQLite BUSY, including an OperationalError without sqlite_errorcode
- THEN rollback is attempted before unlock
- AND the original exception propagates unchanged
