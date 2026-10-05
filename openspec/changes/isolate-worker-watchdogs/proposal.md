# Isolate worker watchdogs

## Why
The deployed supervisor derives both first and derived PID files only from the shared DB path. Its heartbeat query is also global. A sibling can overwrite/unlink the target, and a healthy sibling can mask a stalled worker. The reboot launcher quotes the first supervisor path and service name as one argument. Existing startup tests read the main checkout and inspect comments instead of executable order.

## What Changes
- Namespace PID files by the existing service name; verify current-child ownership before any signal.
- Read only the target child's heartbeat from both existing stores, with startup grace and fail-closed unknown outcomes.
- Bind destructive watchdog signals to a verified Linux pidfd, not an unverified numeric PID.
- Route first-worker boot through the existing clean wrapper and clean inherited Python environment for derived boot.
- Test candidate-relative source and bounded, disposable process/SQLite fixtures.

## Impact
scripts/algotrader-supervisor.sh, scripts/startup-algotrader.sh, a stdlib watchdog helper if needed, and isolated supervisor/startup tests. Production activation requires reviewed release, backup and a separately verified supervisor transition.

## Non-Goals
No scheduler edits, bar/data repair, denominator/universe changes, client changes, cycle-ledger or ingestion-freshness implementation. This change does not establish ETF coverage, freshness <=4h, or seven-day acceptance.
