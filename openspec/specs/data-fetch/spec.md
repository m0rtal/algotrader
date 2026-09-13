# data-fetch Specification

## Purpose

The data-fetch capability defines how market data is acquired, stored, and kept healthy in the algotrader SQLite database. The data-fetch spec covers universe discovery (which instruments are ingested), historical bars (raw daily OHLCV), incremental updates (the guardian that keeps data fresh), and data quality (per-figi health reports and recovery queue).

## Requirements

### Requirement: Historical completeness is detected as a per-figi sub-problem

The system SHALL compute, for every figi whose `first_bar` is older
than one trading day, the exact number of trading days between
`first_bar` and `today` using the `moex_holidays` table to subtract
non-trading days. When `actual_bars < expected_bars * 0.95`, the
`INCOMPLETE_HISTORY` sub-problem SHALL appear in the
`HealthReport.issues` list with a `-25` contribution to the
health-score penalty.

#### Scenario: gap detected inside history

- GIVEN a figi with bars from 2025-01-01 to 2026-09-12, but with a
  14-calendar-day hole in May 2025 (no rows for 2025-05-09 through
  2025-05-22)
- WHEN `compute_all` runs
- THEN `actual_bars < expected_bars * 0.95`
- AND `INCOMPLETE_HISTORY` is in the report's `issues`
- AND the report's `health_score` is at most 75

#### Scenario: holidays do not contribute to expected_bars

- GIVEN a figi with bars every weekday from 2024-01-01 to
  2024-04-30 (no holiday gaps in the data)
- WHEN `compute_all` runs with `moex_holidays` populated for
  2024-Q1 (New Year block, Defender Day, International Women's Day)
- THEN `expected_bars` equals the weekday count minus the holiday
  count for that range
- AND no `INCOMPLETE_HISTORY` issue is raised

### Requirement: Daily guardian backfills detected gaps

After the existing `recover_stale` phase, the daily guardian SHALL
run a `completeness_backfill` phase that:

1. Iterates the figis whose `HealthReport.issues` contains
   `INCOMPLETE_HISTORY`.
2. For each figi, walks `bars` in chronological order and emits
   gap intervals `(start, end)` where the day span exceeds
   `min_gap_days` (default 5) minus any MOEX holidays in range.
3. For each emitted gap, calls the broker's `get_candles` with
   `date_from = start + 1 day` and `date_to = end`, then writes
   the resulting closed candles to SQLite via
   `replace_bars_for_figi(replace=True)`.
4. Marks the figi `completeness_exhausted` when at least one gap
   was found but zero bars were returned (broker has no data for
   the range). Skips `completeness_exhausted` figis on subsequent
   runs.
5. Appends a row to the `pipeline` table with the run summary
   (`figis_examined`, `gaps_found`, `bars_added`, `exhausted`).

#### Scenario: missing interval gets backfilled

- GIVEN a figi with bars ending 2024-03-15 and resuming
  2024-03-25 (no rows for 7 calendar days, 5 trading days
  excluding weekends)
- WHEN the daily guardian runs
- THEN the broker receives one `get_candles` call for
  `date_from=2024-03-16 date_to=2024-03-24`
- AND the resulting bars are written to `bars` for that figi
- AND `bars_count` for the figi increases by the number of bars
  returned (5 if the broker has full data, less if not)

#### Scenario: exhausted figi is not re-attempted

- GIVEN a figi has `last_run_status='completeness_exhausted'`
- WHEN the daily guardian runs
- THEN the completeness pass skips this figi
- AND no broker call is made for it
- AND `bars_added` is not incremented for it

### Requirement: MOEX holiday calendar is stored in the app database

The system SHALL persist a MOEX trading-day calendar in a
`moex_holidays` table with one row per non-trading date. The
calendar is loaded once per refresh via
`scripts/import_moex_holidays.py` and is the source of truth for
"this date is a trading day" used by the health and completeness
modules.

#### Scenario: holiday table populated from JSON

- GIVEN `apps/api/scripts/data/moex_holidays.json` contains 75
  entries spanning 2020..2027
- WHEN `python -m scripts.import_moex_holidays` runs against the
  prod DB
- THEN `SELECT COUNT(*) FROM moex_holidays` returns 75
- AND the import is idempotent (running it twice doesn't duplicate)




### Requirement: Corporate actions are sourced from multiple providers and merged via a common writer

The system SHALL populate the `corporate_actions` table from at least
three sources, all writing through a single common helper
`merge_into_corporate_actions(db_path, rows)` which uses
`INSERT OR REPLACE` on the `(figi, action_type, ex_date)` primary key:

1. The bundled `apps/api/scripts/data/splits_curated.json` — a static
   list of historical splits for Russian and global paper not
   available from any free API.
2. The Tinkoff Invest SDK
   (`client.instruments.get_dividends(figi, from_, to)`) — covers
   every figi known to the broker; sandbox-friendly.
3. The MOEX ISS REST API
   (`GET /iss/securities/{secid}/dividends.json`) — free, no auth;
   `figi → secid` is resolved via `instruments.ticker`.

#### Scenario: each source writes through the common helper

- GIVEN the `corporate_actions` table is empty
- WHEN `python -m scripts.import_splits_curated state.db` is run
- THEN `CorporateActionRow(figi=..., action_type='split', ...)` is
  constructed from each JSON entry
- AND `merge_into_corporate_actions` is called with all rows
- AND the table has 17 rows after the call

#### Scenario: re-running an importer is idempotent

- GIVEN `corporate_actions` has 17 rows from the curated importer
- WHEN `python -m scripts.import_splits_curated state.db` is run a
  second time
- THEN no error is raised
- AND the table still has 17 rows
- AND no duplicates exist (PK constraint + INSERT OR REPLACE)

#### Scenario: source conflict is resolved by last writer

- GIVEN a `(figi, action_type, ex_date)` row exists with
  `cash_amount=100.0, note='old'`
- WHEN another importer writes the same PK with
  `cash_amount=387.0, note='new'`
- THEN the row is updated (INSERT OR REPLACE) to `cash_amount=387.0,
  note='new'`
- AND no duplicate row is created

#### Scenario: MOEX importer skips figis with no resolvable secid

- GIVEN a figi with `instruments.ticker = NULL` (e.g. recent IPO not
  yet indexed)
- WHEN `python -m scripts.import_corporate_actions_moex state.db` is run
- THEN the importer silently skips that figi
- AND no error is raised

#### Scenario: MOEX importer survives network failures

- GIVEN the MOEX ISS endpoint is unreachable
- WHEN `python -m scripts.import_corporate_actions_moex state.db` is run
- THEN the importer logs no errors and exits 0
- AND the rows from previously-imported sources remain in the table

### Requirement: Corporate action sources are exposed as operator scripts with idempotent semantics

The system SHALL expose each of the three sources as a
`python -m scripts.<name>` entry point backed by an in-package
implementation in `algotrader_api.scripts_import`. Every operator
script MUST be safe to re-run without duplicating rows.

#### Scenario: operator runs all three scripts in sequence

- GIVEN the `corporate_actions` table is empty
- WHEN the operator runs, in order:
  - `python -m scripts.import_splits_curated state.db`
  - `python -m scripts.import_corporate_actions_tinkoff state.db`
  - `python -m scripts.import_corporate_actions_moex state.db`
- THEN the table has at minimum the curated splits (17)
- AND any dividends returned by Tinkoff
- AND any dividends returned by MOEX ISS for the same figis

#### Scenario: re-running the same script is a no-op on existing rows

- GIVEN a row exists from a previous run
- WHEN the operator runs the same script again
- THEN the table row count does not increase (no duplicates)
- AND any updated values (e.g. `cash_amount` correction) are written
