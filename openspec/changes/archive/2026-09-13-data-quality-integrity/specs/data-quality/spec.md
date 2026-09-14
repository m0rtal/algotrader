# data-quality Specification (delta)

## Purpose

The data-quality capability defines the *shape* of stored market
data: how corporate actions (splits and dividends) are recorded, how
backtests read an adjusted close, and how the system detects bar-level
corruption at ingest time. The capability complements the data-fetch
capability (which owns ingestion and coverage); data-quality is
about the integrity and adjustability of the data once stored.

## ADDED Requirements

### Requirement: Corporate actions are persisted and applied as a backward-adjusted close view

The system SHALL persist per-figi corporate actions (stock splits and
dividends) in the `corporate_actions` table and SHALL expose the
`bars_adjusted` view that computes a backward-adjusted close per bar.

#### Scenario: pre-split bar shows scaled-down adj_close

- GIVEN a figi with a 2-for-1 split on 2025-06-01
- AND a pre-split bar at 2025-05-15 with `close=200`
- WHEN `SELECT adj_close FROM bars_adjusted WHERE ts='2025-05-15'` runs
- THEN it returns `100.0`

#### Scenario: bar with no events leaves adj_close equal to close

- GIVEN a figi with no rows in `corporate_actions`
- AND any bar in `bars`
- WHEN `SELECT close, adj_close FROM bars_adjusted WHERE figi=?` runs
- THEN `adj_close` equals `close`

#### Scenario: corporate_actions import is idempotent

- GIVEN `apps/api/scripts/data/corporate_actions.json` loaded once
- WHEN the operator runs `python -m scripts.import_corporate_actions`
  a second time against the same database
- THEN the `corporate_actions` row count is unchanged
- AND no `UNIQUE constraint failed` exception is raised

### Requirement: Every fetched bar is validated before write

The ingestion path SHALL run `validate_bar` on every candle before
writing it to the `bars` table. Bars that violate any rule (high below
o/h/l/c, low above o/h/l/c, negative volume, all-zero, missing field)
SHALL be skipped and recorded in `ingestion_logs` with level `warn`.

#### Scenario: corrupt bar is skipped and logged

- GIVEN a fetched candle with `high=85, open=90` (high < open)
- WHEN `BackfillRunner._backfill_one` runs
- THEN the candle is NOT written to `bars`
- AND a row is inserted into `ingestion_logs` with `level='warn'`
  and `figi=<the figi>`

#### Scenario: valid bar in a mixed batch is written

- GIVEN a fetched batch containing one invalid candle
  (`high < open`) and one valid candle
- WHEN `BackfillRunner._backfill_one` runs
- THEN the invalid candle is dropped
- AND the valid candle is written to `bars`
- AND `ingestion_logs` records exactly one skipped row

### Requirement: Stored-bar corruption surfaces as BAR_CORRUPTION

The per-figi health report SHALL list `BAR_CORRUPTION` whenever at
least one stored bar for that figi violates any integrity rule
(high < max(open, low, close), low > min(open, high, close),
volume < 0, or all-zero open/high/low/close). The
`BAR_CORRUPTION` issue SHALL impose a `-40` penalty on the
`health_score`.

#### Scenario: corruption penalises the health score

- GIVEN a figi with a mix of valid and corrupt stored bars
- WHEN `compute_health(db, figi)` runs
- THEN `HealthIssue.BAR_CORRUPTION` is in `report.issues`
- AND `report.health_score <= 60`
