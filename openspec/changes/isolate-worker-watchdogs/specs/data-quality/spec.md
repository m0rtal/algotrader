# data-quality Specification (delta)

## ADDED Requirements

### Requirement: Role-Isolated Worker Supervision

Each named worker supervisor SHALL use a service-specific PID file derived as `${STATE_DB}.${NAME}.worker.pid`, shared consistently by its own main loop and watchdog. It SHALL NOT overwrite, remove or signal a sibling's worker target. Valid existing names SHALL remain unchanged; unsafe filename components SHALL be rejected. Existing cwd/DB resolution, default polling interval 30 seconds, restart delay 5 seconds and heartbeat threshold 0.0208 days SHALL remain unchanged. The watchdog SHALL read SQLite read-only, scope heartbeat evidence to its verified current child, and distinguish freshness from sibling/global heartbeat activity. It SHALL use Linux pidfd for a stale-child SIGKILL after verifying parent ownership, process start identity and unchanged PID-file target; unsupported/unverifiable conditions SHALL never fall back to an unsafe numeric signal. Unknown diagnostics SHALL be bounded and non-secret. No schedule, bars, coverage denominator, universe or upstream trust rule SHALL change.

#### Scenario: Two roles share a database without sharing ownership

- GIVEN first and derived supervisors use the same API cwd and state database
- WHEN both launch owned temporary worker children and one exits or its PID file is removed
- THEN their PID paths SHALL differ and the sibling PID file and worker SHALL remain unchanged

#### Scenario: A healthy sibling cannot mask a stalled child

- GIVEN a verified current child has an own stale heartbeat and its sibling has a fresh heartbeat in either existing store
- WHEN its supervisor watchdog checks liveness
- THEN it SHALL classify its own child as stale and signal only the verified owned child through pidfd
- AND the sibling SHALL remain untouched

#### Scenario: Heartbeat ownership includes current process lifetime

- GIVEN pipeline heartbeat detail is exact `pid=<child>` or pipeline_heartbeat has matching worker_pid
- WHEN the watchdog selects evidence
- THEN other-PID rows and rows older than the current child's start second SHALL NOT establish freshness
- AND a missing own heartbeat SHALL receive startup grace equal to the existing age threshold from actual child start, not from repeated polls

#### Scenario: Unsafe or unknown checks never signal

- GIVEN a malformed PID file, sibling/non-child PID, changed or reused process identity, changed PID target, unreadable/missing database, malformed/future heartbeat timestamp or unavailable pidfd capability
- WHEN the watchdog checks liveness
- THEN it SHALL leave all processes and SQLite state untouched and emit only a bounded unknown diagnostic
- AND any opened pidfd SHALL close on every outcome

### Requirement: Correct Clean Worker Bootstrap

The reboot launcher SHALL start the first worker through the existing clean supervisor wrapper, whose exact role is `worker.py daily first`. It SHALL clear inherited PYTHONPATH and PYTHONHOME for derived-worker launch while retaining `worker.py daily derived` and API cwd. It SHALL preserve API-health-before-workers ordering and refusal on failed health. Startup regression tests SHALL inspect candidate-relative executable statements, not a fixed production checkout or incidental comments.

#### Scenario: First and derived launch arguments are executable and role-specific

- GIVEN the candidate startup and clean-wrapper source
- WHEN bounded offline tests inspect or capture launch arguments with all external operations stubbed into owned temporary fixtures
- THEN first launch SHALL use the clean wrapper without a combined script-path/service argument
- AND derived launch SHALL retain exact role/cwd and sanitized Python environment

#### Scenario: Bootstrap ordering is independent of comments and checkout location

- GIVEN candidate checkout fixtures whose comments differ from production source
- WHEN startup ordering tests run
- THEN actual API launch and successful health wait SHALL precede both worker launches
- AND moving comments SHALL NOT change the ordering verdict
- AND no production script, process, scheduler, database or log SHALL be touched
