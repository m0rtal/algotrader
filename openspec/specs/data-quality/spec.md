# Data quality

## Purpose

The data-quality capability ensures that the algorithmic-trading pipeline
operates against complete, consistent, and verifiable market data. It
covers:

- **Health scoring** — per-ticker health reports that surface sparse
  history, gaps, missing recent days, orphan data, and incomplete
  history.
- **Integrity validation** — bar-level rules that reject corrupt OHLCV
  rows before they reach backtests.
- **Recovery queue** — prioritised list of figis needing backfill,
  ordered by health score × expected bars per day.
- **Daily guardian** — systemd-style timer that runs the recovery queue
  and a completeness backfill pass once per day.
- **Corporate-action ingestion** — historical splits (derived from
  bars) and dividends (Tinkoff / MOEX ISS) so backtests can compute
  `adj_close`.

## Requirements

### Requirement: Historical splits are derived from local bars

The system SHALL detect historical stock splits and consolidations by
diffing consecutive `bars` rows in the local SQLite `bars` table, with
the **current** `face_value` fetched from MOEX ISS as a verification
cross-check.

#### Scenario: Single 2-for-1 split detected

Given `bars` for figi `X` contain rows `close=100` at `t-1` and
`close=50` at `t`, AND the current `face_value` for `X` is `2.0` (was
`1.0` before the split),
When the derivation runs,
Then `corporate_actions` SHALL contain one row
`(figi='X', action_type='split', ex_date=t, factor=2.0, source LIKE 'derived:bars+%')`.

#### Scenario: Reverse 10-for-1 split detected

Given `bars` for figi `Y` contain rows `close=5` at `t-1` and `close=50`
at `t`, AND `volume` is unchanged across the transition,
When the derivation runs,
Then `corporate_actions` SHALL contain one row
`(figi='Y', action_type='split', ex_date=t, factor=10.0, source LIKE 'derived:bars+%')`.

#### Scenario: Sub-threshold price changes ignored

Given `bars` for figi `Z` contain rows `close=100` at `t-1` and
`close=140` at `t` (ratio 1.4 — within the 2× threshold),
When the derivation runs,
Then no `corporate_actions` row SHALL be written for `Z` at `t`.

#### Scenario: Multiple splits on a single ticker

Given `bars` for figi `W` contain three transitions, each with
`ratio <= 0.5` or `ratio >= 2.0`,
When the derivation runs,
Then `corporate_actions` SHALL contain one row per detected transition.

#### Scenario: Bonus issue (BONU) is not misclassified as reverse split

Given `bars` for figi `V` contain `close=100, volume=1000` at `t-1` and
`close=10, volume=10000` at `t` (volume scaled by 10× same as price
ratio),
When the derivation runs,
Then no `corporate_actions` row SHALL be written for `V` at `t`
(treated as BONU, not reverse split).

#### Scenario: Re-run is idempotent

Given `corporate_actions` already contains a `derived:*` row for figi
`X` at `t`,
When the derivation runs again,
Then no new row SHALL be written and the existing row SHALL be
unchanged.

### Requirement: Bond Depth Backfill

`backfill_bonds_to_depth(target_days=30)` SHALL be implemented in
`apps/api/src/algotrader_api/ingestion/backfill.py`. For each tradable
bond figi (class='bond') with fewer than `target_days` bars in the
`bars` table, it SHALL fetch historical bars from Tinkoff
`GetHistoricalBonds` and insert them (skipping duplicates by
`(figi, ts)`) until either `target_days` is reached or the broker's
earliest-available date is exhausted. The function SHALL be invoked
from a new `_step_bonds_depth` daily chain step in
`apps/api/worker.py`, AFTER `_step_backfill_moex` and BEFORE
`_step_corporate_actions`.

#### Scenario: Sparse bond is brought to target depth

- GIVEN a bond figi with 15 bars in `bars` table
- AND `backfill_bonds_to_depth(target_days=30)` is called
- WHEN the function executes
- THEN Tinkoff `GetHistoricalBonds` is called for that figi
- AND new bars are inserted until the count is `>= 30` or the broker
  has no more history
- AND a structured log line is emitted:
  `{event: bond_depth_backfill, figi, before: 15, after: 30, added: 15}`

#### Scenario: Full bond is skipped

- GIVEN a bond figi with 250 bars in `bars` table
- WHEN `backfill_bonds_to_depth(target_days=30)` is called
- THEN the function does NOT call Tinkoff for that figi
- AND no log line is emitted for that figi

#### Scenario: Bond with zero bars is fully backfilled

- GIVEN a bond figi with 0 bars in `bars` table
- AND the broker has 365 days of history
- WHEN `backfill_bonds_to_depth(target_days=30)` is called
- THEN Tinkoff `GetHistoricalBonds` is called
- AND bars are inserted until count is `>= 30`
- AND no more than `target_days * 1.5` bars are inserted (sanity bound)

### Requirement: Bond Priority Parity

`BOND_PRIORITY` constant SHALL be defined in
`apps/api/src/algotrader_api/ingestion/queue.py` with the same numeric
value as `SHARE_PRIORITY`. Queue dispatch SHALL use `BOND_PRIORITY`
when selecting bond figis for the next backfill slot.

#### Scenario: Bond figis compete equally with shares

- GIVEN 10 share figis and 10 bond figis in the queue
- AND all have equally-old `last_backfilled_ts`
- WHEN the queue dispatcher selects the next 10 figis for backfill
- THEN the selection includes at least one bond figi

### Requirement: Pre-Consumption Coverage Gate

`features.build_features(figis, window)` SHALL be implemented in
`apps/api/src/algotrader_api/ml/features.py`. Before building the
feature matrix, it SHALL call `check_coverage(figis)`. If any figi in
`figis` fails the coverage check, the function SHALL trigger
`auto_recovery(failing_figis)` exactly once and re-check. If still
failing, it SHALL raise `InsufficientDataError` with the list of
failing figis and `attempted_recovery=True`.

#### Scenario: All figis have full coverage

- GIVEN all figis in the feature set have `max_ts` on or after the last completed MOEX business day (weekends and `moex_holidays` excluded)
- AND all have a positive `expected_bars` cache value
- AND all have `bars_count >= 0.95 * expected_bars`
- WHEN `build_features(figis)` is called
- THEN it returns the feature matrix
- AND no error is raised

#### Scenario: One figi is stale (max_ts < yesterday)

- GIVEN 1 figi out of 10 has `max_ts` = 5 days ago
- WHEN `build_features(figis)` is called
- THEN `auto_recovery` is invoked with that figi
- AND the auto-recovery calls Tinkoff for that figi
- AND `check_coverage` is re-run
- AND if recovery succeeds, the feature matrix is returned
- AND if recovery fails, `InsufficientDataError` is raised with
  `{figi, max_ts, reason: 'stale'}`

#### Scenario: One figi is incomplete (bars < 95% of expected)

- GIVEN 1 figi out of 10 has `bars_count` = 100 bars
- AND `expected_bars` = 252 (1 year of business days)
- WHEN `build_features(figis)` is called
- THEN `auto_recovery` is invoked
- AND `backfill_bonds_to_depth` is called for that figi (if class='bond')
- OR Tinkoff direct fetch is called (for non-bond classes)
- AND if recovery succeeds, feature matrix is returned
- AND if recovery fails, `InsufficientDataError` is raised with
  `{figi, bars_count, expected, reason: 'incomplete'}`

#### Scenario: Auto-recovery triggers exactly once

- GIVEN a figi with insufficient coverage
- AND auto-recovery runs Tinkoff but is rate-limited mid-flight
- WHEN `build_features(figis)` is called
- THEN `auto_recovery` raises an exception (no second retry)
- AND the exception propagates to the caller
- AND the log shows `{event: insufficient_data_error, attempted_recovery: true}`

#### Scenario: Last completed session falls before a weekend or holiday

- GIVEN today is Monday or the day after a MOEX holiday
- AND a figi has a bar on the last completed MOEX business day
- AND its `expected_bars` is positive and coverage is at least 95%
- WHEN `check_coverage(figis)` is called
- THEN that figi is not stale solely because the exchange was closed since that bar

#### Scenario: Coverage denominator is unknown

- GIVEN a figi has `expected_bars` NULL or zero
- WHEN `check_coverage(figis)` is called
- THEN that figi fails the gate with `reason: 'unknown_expected'` when its last bar is fresh
- AND no instrument class is silently excluded from the check

### Requirement: Expected Bars Caching

`instruments.expected_bars` column SHALL be added via migration
`apps/api/src/algotrader_api/db/migrations/023_instruments_expected_bars.sql`.
The column SHALL cache the result of `expected_business_days(listing_date,
yesterday)` for each figi, computed once via the one-shot script
`apps/api/scripts/populate_expected_bars.py` and refreshed after every
bond backfill pass that affects a figi's listing window.

#### Scenario: expected_bars column is populated

- GIVEN migration 023 has been applied
- WHEN `populate_expected_bars.py` is run
- THEN `instruments.expected_bars` is set for every tradable figi
- AND the value equals `expected_business_days(listing_date, yesterday)`
- AND the script is idempotent (re-running produces the same values)

#### Scenario: expected_bars lookup is O(1)

- GIVEN `instruments.expected_bars` is populated
- WHEN `check_coverage(figis)` is called
- THEN it reads `expected_bars` via a single SQL SELECT per figi
- AND it does NOT call `expected_business_days()` (which would be O(days))

### Requirement: Migration Tracking via schema_migrations Table

The system SHALL record every successfully applied migration in a
`schema_migrations` table with the columns `(migration_id TEXT PRIMARY
KEY, content_hash TEXT NOT NULL, applied_at TIMESTAMP NOT NULL DEFAULT
CURRENT_TIMESTAMP)`.

#### Scenario: schema_migrations table is created on first run

- GIVEN a fresh SQLite database with no `schema_migrations` table
- WHEN the migration runner starts
- THEN it creates the `schema_migrations` table with the specified
  columns
- AND continues with normal migration application

#### Scenario: Each applied migration is recorded

- GIVEN the migration runner applies migration `016_instruments_figi_pk.sql`
  successfully
- WHEN the runner commits the migration pass
- THEN a row exists in `schema_migrations` with `migration_id` equal to
  `016_instruments_figi_pk.sql` and `content_hash` matching the
  SHA-256 of the file's contents

### Requirement: Migration Runner Skip-by-Hash

The migration runner SHALL skip migration files whose
`(migration_id, content_hash)` pair already exists in
`schema_migrations`. A hash mismatch (file changed after application)
SHALL raise an error and halt the runner — silent re-application of
mutated SQL is forbidden.

#### Scenario: Already-applied migration is skipped

- GIVEN migration `022_dividends_throttle_pending.sql` is recorded in
  `schema_migrations` with hash `abc123`
- WHEN the runner loads `022_dividends_throttle_pending.sql` whose
  current content hashes to `abc123`
- THEN the runner skips execution of that file
- AND logs `migration.skip reason=hash_match file=022_dividends_throttle_pending.sql`

#### Scenario: Hash mismatch halts the runner

- GIVEN migration `016_instruments_figi_pk.sql` is recorded in
  `schema_migrations` with hash `old_hash`
- WHEN the runner loads `016_instruments_figi_pk.sql` whose current
  content hashes to `new_hash` (different from `old_hash`)
- THEN the runner raises a `MigrationHashMismatch` exception
- AND no further migrations are processed in the current run

### Requirement: Per-Statement Savepoint Wrap

The migration runner SHALL wrap each statement in a SAVEPOINT before
execution and RELEASE it on success. If a statement raises, the runner
SHALL ROLLBACK TO the savepoint, surface the error, and continue with
the next statement. A statement whose error matches the
swallowed-error list SHALL be silently skipped (current behavior
preserved).

#### Scenario: Statement failure rolls back to its savepoint

- GIVEN a migration file with statements A, B, C
- WHEN statement B raises `sqlite3.OperationalError`
- THEN savepoint for B is rolled back
- AND statements A's changes are committed
- AND statement C is attempted next (not aborted by B's failure)
- AND the swallowed-error check applies: if B's error is in the
  swallowed list, execution continues silently; otherwise the runner
  surfaces the error and continues processing remaining statements

#### Scenario: All statements in a migration succeed

- GIVEN a migration file with statements A, B, C
- WHEN all three succeed
- THEN all three savepoints are released
- AND the migration is recorded as applied in `schema_migrations`

### Requirement: Migration 016 Idempotent

Migration `016_instruments_figi_pk.sql` SHALL be idempotent: on every
run, it SHALL detect the current state and run only the branches
appropriate to that state. The three states are `cleanup`
(instruments missing, instruments_new present), `rebuild` (instruments
has PRIMARY KEY on `ticker`), and `noop` (clean state).

#### Scenario: Cleanup branch runs when instruments is missing

- GIVEN the database has `instruments_new` but no `instruments`
- WHEN migration 016 runs
- THEN it executes the `cleanup` branch
- AND renames `instruments_new` to `instruments`
- AND the `rebuild` branch is NOT executed

#### Scenario: Rebuild branch runs on first apply

- GIVEN the database has `instruments` with PRIMARY KEY on `ticker`
- AND no `instruments_new`
- WHEN migration 016 runs
- THEN it executes the `rebuild` branch (full table-rebuild dance)
- AND the `cleanup` branch is NOT executed

#### Scenario: Noop branch runs on a clean database

- GIVEN the database has `instruments` with no PRIMARY KEY on `ticker`
- AND no `instruments_new`
- WHEN migration 016 runs
- THEN neither the `cleanup` nor the `rebuild` branch is executed
- AND the file's net effect is a no-op

### Requirement: Sequential Bootstrap Ordering

`scripts/startup-algotrader.sh` SHALL launch the API supervisor first
and SHALL block until the API `/health` endpoint returns
`status=ok` before launching any worker supervisor. If the API does
not become healthy within 30 seconds (30 attempts × 1 second sleep),
the script SHALL exit with a non-zero status and SHALL NOT launch the
worker supervisors.

#### Scenario: API becomes healthy before workers start

- GIVEN the API supervisor has been launched by the startup script
- WHEN the API `/health` endpoint returns `status=ok` within 30
  attempts
- THEN the script proceeds to launch the worker supervisors

#### Scenario: API fails to become healthy

- GIVEN the API supervisor has been launched by the startup script
- AND the API `/health` endpoint does NOT return `status=ok` within 30
  attempts
- WHEN the retry budget is exhausted
- THEN the script prints an error message to stderr
- AND exits with non-zero status
- AND does NOT launch the worker supervisors

### Requirement: One-Shot Repair Script

`scripts/repair_instruments_table.py` SHALL detect the broken state
(`instruments` missing, `instruments_new` present) on a production
database, finish the interrupted migration 016 by renaming
`instruments_new` to `instruments`, recreate the `ml_features` view,
and reconcile the recorded `schema_migrations.content_hash` for
migration 016 with the current on-disk hash. The script SHALL have no
package imports (stdlib only) and SHALL read the database path from
the `ALGOTRADER_STATE_DB` environment variable with a hard-coded
fallback to the production path.

#### Scenario: Repairing a broken production database

- GIVEN a database with `instruments_new` present and `instruments`
  absent
- AND `schema_migrations` records the OLD hash for
  `016_instruments_figi_pk.sql`
- WHEN the operator runs `python scripts/repair_instruments_table.py`
- THEN `instruments_new` is renamed to `instruments`
- AND the `ml_features` view is recreated
- AND `schema_migrations.content_hash` for
  `016_instruments_figi_pk.sql` is updated to match the on-disk file

#### Scenario: Repair script is a no-op on a clean database

- GIVEN a database with `instruments` present and no `instruments_new`
- WHEN the operator runs `python scripts/repair_instruments_table.py`
- THEN the script prints "state already clean"
- AND the recorded hash is reconciled to the on-disk file

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

### Requirement: Truthful Best-Effort Trailing Gap Recovery

The worker gap-recovery step SHALL report `False` when a trailing FIGI attempt raises an ordinary exception or emits an existing `ticker_progress` event with status `error`, while continuing subsequent trailing attempts unless existing metadata lock deferral or cancellation requires exit. The ordinary summary SHALL include bounded numeric `failed=N`, counting unique failed trailing FIGIs across exceptions and events once, and SHALL retain accurate historical, trailing, and source totals using existing runner integer counts. It SHALL preserve committed bars, valid zero-row results, one-loop owned-client cleanup, cancellation propagation, existing historical handling, and metadata lock deferral. Gap recovery SHALL remain best-effort: subsequent daily phases run and the cycle returns `rc=1` after failure. Newly introduced trailing failure diagnostics SHALL NOT copy raw exception or event error payloads.

#### Scenario: Raised trailing failure does not become success

- GIVEN multiple trailing FIGIs and an ordinary failure for one FIGI
- WHEN the worker runs gap recovery
- THEN later trailing FIGIs are attempted and the step returns `False` with `failed=1`
- AND summary row counts retain successfully returned additions
- AND new trailing warnings contain the exception type, not its raw message

#### Scenario: Actual runner error event is observed

- GIVEN actual BackfillRunner candle fetch chunks all fail for a trailing FIGI
- WHEN the runner emits `ticker_progress` status `error` and returns `0`
- THEN the worker returns `False` with `failed=1`
- AND warnings or unrelated events alone do not increase the count

#### Scenario: Failure signals count unique FIGIs

- GIVEN one trailing FIGI emits repeated error events and then raises an ordinary exception
- WHEN the worker aggregates trailing failures
- THEN that FIGI contributes exactly one to `failed=N`
- AND historical events and events for an unrelated FIGI do not contribute

#### Scenario: Partial success survives sibling failure

- GIVEN a successful FIGI commits a bar and another trailing FIGI fails
- WHEN the step finishes
- THEN the committed bar remains and the step returns `False`
- AND historical, trailing, moex, and tinkoff counters reflect their successful returned additions

#### Scenario: Empty and no-work results stay valid

- GIVEN no gaps or valid empty or idempotent trailing responses without an error signal
- WHEN the worker runs gap recovery
- THEN zero added rows alone do not cause `False`
- AND a no-gap run retains its existing no-gap detail

#### Scenario: Existing lock meanings stay distinct

- GIVEN actual writer lock contention during trailing recovery
- WHEN metadata acquisition raises WriterLockBusy with role `backfill-metadata`
- THEN the step retains `False` with existing structured `DEFER writer-lock-busy` with `result=deferred` rather than changing its tuple into process exit 75
- AND a bar-writer timeout instead produces an ordinary failed trailing summary
- AND ordinary exceptions are not relabelled BUSY

#### Scenario: One-loop cleanup and cancellation remain intact

- GIVEN historical and trailing jobs use one owned client
- WHEN they finish or direct asyncio cancellation interrupts the trailing await
- THEN started coroutine jobs settle before exactly-once awaited client close on their shared event loop
- AND cancellation propagates rather than becoming an ordinary failure result

#### Scenario: Best-effort failure degrades the daily cycle

- GIVEN gap recovery returns `False`
- WHEN the daily chain runs
- THEN later phases still execute and phase status is `error` with cycle `rc=1`
- AND migrations, universe_sync, and backfill_moex retain their existing critical behavior
