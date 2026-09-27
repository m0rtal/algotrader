# data-quality Specification (delta)

## ADDED Requirements

### Requirement: Autonomous Pipeline Liveness

The system SHALL run a continuous data-pipeline process whose lifecycle
is supervised by the existing `algotrader-supervisor.sh` and whose
status is observable from the API and the UI.

#### Scenario: Worker process runs in `live` mode

Given the system is started (`@reboot` cron entry or operator action),
When the supervisor launches the worker,
Then the worker SHALL run the daily chain in a loop with a sleep
interval of `LIVE_INTERVAL_SECONDS` (default 1800) between cycles,
AND the supervisor SHALL restart the worker if it exits with rc≠0
within 30 seconds,
AND the worker SHALL write one row per cycle to the `pipeline_runs`
table with `cycle_id`, `started_at`, `finished_at`, `rc`, and
`stale_2d_count` (computed at cycle end).

#### Scenario: Worker writes a heartbeat every 30 seconds

Given the worker is running a cycle,
When 30 seconds have elapsed since the last heartbeat write,
Then the worker SHALL write (or update) a row in `pipeline_heartbeat`
with `worker_pid`, `phase`, `last_bar_ts`, `updated_at`.

#### Scenario: Stale heartbeat triggers supervisor restart

Given the most recent row in `pipeline_heartbeat` is older than 5 minutes,
When the cron watchdog `cron_liveness_check.sh` runs (every 2 minutes),
Then the watchdog SHALL kill the worker process AND the supervisor
SHALL restart it within 30 seconds.

#### Scenario: Pipeline age older than 4 hours is reported as STALE

Given the most recent `pipeline_runs` row has `finished_at` older than
4 hours ago,
When `GET /api/admin/backfill/status` is called,
Then the response SHALL include `last_cycle_age_seconds` > 14400,
AND the frontend SHALL render the `<StatusBanner>` in red.

### Requirement: Auto-recovery fetches bonds via real Tinkoff API

The system SHALL, when `auto_recovery()` runs inside
`build_features()` and identifies stale bond figis, fetch missing
history using the broker SDK method that exists on `RealTinkoffClient`.

#### Scenario: Auto-recovery calls `get_candles`, not `get_historical_bonds`

Given `build_features(conn, [stale_bond_figi])` is called,
When the gate raises `InsufficientDataError` and `auto_recovery` runs,
Then the recovery path SHALL invoke `client.get_candles(figi, from_, to_)`
(the method that exists on `RealTinkoffClient`),
AND SHALL NOT invoke `client.get_historical_bonds(...)` (which does
not exist in production code).

#### Scenario: Auto-recovery surfaces a real error, not silent fail

Given `client.get_candles` raises `TinkoffError` for a figi,
When auto_recovery runs,
Then the error SHALL be logged as `auto_recovery_fetch_failed` with
`figi` and `error[:200]`,
AND the call SHALL NOT be silently swallowed (the existing
`AdaptiveRetry` policy applies),
AND `InsufficientDataError` SHALL be re-raised to the caller.

#### Scenario: Auto-recovery refills bond history up to `expected_bars`

Given a bond figi `X` has `expected_bars=1448` and the bars table has
`1100` rows for `X`,
When auto_recovery runs and `client.get_candles` returns 400 candles
covering the gap window,
Then `bars` for `X` SHALL contain at least `1500` rows after recovery,
AND `check_coverage()` SHALL re-evaluate and return `ok=True` for `X`.

### Requirement: Pipeline Status API exposes liveness

The system SHALL expose liveness fields on
`GET /api/admin/backfill/status` so the frontend can render
freshness state without additional round-trips.

#### Scenario: Status response includes last-cycle age

Given at least one row exists in `pipeline_runs`,
When `GET /api/admin/backfill/status` is called,
Then the response SHALL include `last_cycle_age_seconds` (integer,
`now - max(finished_at)`).

#### Scenario: Status response includes stale counts

Given instruments have varying `max(ts)` across figis,
When `GET /api/admin/backfill/status` is called,
Then the response SHALL include `stale_2d_count`, `stale_1d_count`,
`fresh_count`, `no_bars_count` — the same numbers the UI dashboard
already displays (so the banner does not duplicate the query).

### Requirement: UI surfaces stale pipeline immediately

The system SHALL render a status banner in the Topbar whenever the
pipeline is degraded, with severity scaling by how degraded.

#### Scenario: Red banner appears when stale > 100 OR cycle > 4h

Given `stale_2d_count > 100` OR `last_cycle_age_seconds > 14400`,
When the Topbar mounts,
Then `<StatusBanner variant="red">` SHALL render with text
"PIPELINE STALE — {stale_2d_count} figis outdated, last cycle
{format_age(last_cycle_age_seconds)} ago".

#### Scenario: Yellow banner appears when stale ≤ 100

Given `stale_2d_count > 0 AND stale_2d_count <= 100 AND
last_cycle_age_seconds <= 14400`,
When the Topbar mounts,
Then `<StatusBanner variant="yellow">` SHALL render with text
"{stale_2d_count} figis outdated (recovering)".

#### Scenario: No banner when pipeline is healthy

Given `stale_2d_count == 0 AND last_cycle_age_seconds <= 14400`,
When the Topbar mounts,
Then no `<StatusBanner>` SHALL render.

#### Scenario: Banner survives route changes

Given the user navigates from `/dashboard` to `/data` to `/settings`,
When each route mounts,
Then the `<StatusBanner>` SHALL remain visible (mounted in Topbar,
not in any route-specific component).
