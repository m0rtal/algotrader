# data-fetch Specification (delta)

## ADDED Requirements

### Requirement: Raw OHLCV bars are stored in SQLite alongside parquet

The system SHALL persist every successfully backfilled candle to a
`bars` table in the same SQLite database that already holds
`instruments`, `instrument_metadata`, and `ingestion_logs`, so that
the `/api/bars/<symbol>` drilldown endpoint can serve candles from
SQLite directly without re-scanning the parquet directory on every
request.

#### Scenario: BackfillRunner writes a freshly fetched candle batch into the bars table

- GIVEN a backfill run for figi `BBG004730N88` fetched 250 closed daily
  candles from `client.get_candles(...)`
- AND the parquet writer has successfully appended those candles to
  `bars/SBER.parquet`
- WHEN `BackfillRunner._backfill_one` returns to the caller
- THEN the runner issues a single SQLite transaction containing
  `INSERT OR REPLACE INTO bars (figi, ts, open, high, low, close, volume)
  VALUES (...)` for every candle
- AND `instrument_metadata.total_bars`, `instrument_metadata.first_bar_ts`,
  and `instrument_metadata.last_bar_ts` are updated in the same
  transaction to reflect the new state of the figi
- AND the transaction commits in a single WAL write so that a
  concurrent `/api/bars/<symbol>` reader sees either the pre-run or
  post-run snapshot, never a half-written one

#### Scenario: `/api/bars/<symbol>` serves candles from SQLite without touching parquet

- GIVEN figi `BBG004730N88` has 1300 candles stored in the `bars` table
- WHEN the UI calls `GET /api/bars/BBG004730N88`
- THEN the backend resolves `BBG004730N88` to the figi through the
  `instruments` table
- AND the backend returns `SELECT ts, open, high, low, close, volume
  FROM bars WHERE figi = 'BBG004730N88' ORDER BY ts` as JSON
- AND the request does NOT touch the parquet directory or open a
  DuckDB connection
- AND the round-trip latency is under 100 ms for 1300 rows

#### Scenario: One-shot migration script imports existing parquet into the bars table

- GIVEN the operator runs `python -m scripts.migrate_parquet_to_sqlite`
  while the backend is stopped
- WHEN the script scans `data/bars/*.parquet` via DuckDB
- THEN every candle from every parquet file is written into the
  corresponding `(figi, ts)` row of the `bars` table
- AND `instrument_metadata.first_bar_ts` / `last_bar_ts` /
  `total_bars` are recomputed from `SELECT MIN(ts), MAX(ts),
  COUNT(*) FROM bars WHERE figi = ?`
- AND the migration is idempotent: re-running it does not produce
  duplicate rows or violate the `PRIMARY KEY (figi, ts)` constraint

#### Scenario: Parquet files stay on disk after migration for analytics and fallback

- GIVEN the migration script has finished and the `bars` table is
  populated
- WHEN the operator inspects `data/bars/`
- THEN every original parquet file is still present on disk
- AND the parquet writer path continues to update both `bars` and
  the parquet file on every subsequent backfill
- AND the Drilldown chart, ingest pipeline, and any future
  analytical DuckDB queries can read from parquet without a
  schema change

### Requirement: Bars table has a primary key on (figi, ts) for O(1) upsert and indexed reads

The system SHALL declare `bars` with `PRIMARY KEY (figi, ts)` and a
secondary index on `figi` so that backfill upserts and drilldown
queries are both O(log n) on SQLite.

#### Scenario: Re-running backfill for the same figi overwrites existing rows without duplicates

- GIVEN the `bars` table contains 1300 rows for figi `BBG004730N88`
- WHEN the runner re-fetches those 1300 candles plus 5 new ones and
  inserts them via `INSERT OR REPLACE INTO bars ...`
- THEN the table still contains exactly 1305 rows for that figi
- AND no `UNIQUE constraint failed: bars.figi, bars.ts` exception is
  raised
- AND the new 5 rows overwrite any rows that shared a `(figi, ts)`
  pair with the previous backfill window

#### Scenario: `SELECT` by `figi` is index-driven

- GIVEN the `bars` table has 2.4 million rows across 3700 figis
- WHEN the backend runs `SELECT ts, open, high, low, close, volume
  FROM bars WHERE figi = ? ORDER BY ts`
- THEN the query plan uses the `idx_bars_figi` index (not a full
  table scan)
- AND the result is returned in under 100 ms

### Requirement: Live `instrument_metadata.total_bars` mirrors the bars table

The system SHALL keep `instrument_metadata.total_bars` consistent
with `SELECT COUNT(*) FROM bars WHERE figi = ?` so that the operator-
facing pending counter (`/api/admin/backfill/pending`) and the Bars
tab header (`bars_count` aggregate) reflect the actual SQLite row
count, not a stale metadata field.

#### Scenario: A new backfill run updates total_bars after writing candles

- GIVEN `instrument_metadata.total_bars = 1300` for figi
  `BBG004730N88`
- WHEN the runner writes 5 new candles into the `bars` table
- THEN `instrument_metadata.total_bars = 1305` for that figi
- AND `/api/admin/backfill/pending` reflects the new up-to-date count
- AND `/api/tickets` (Bars tab header) shows `1305` bars for `SBER`

#### Scenario: Stale metadata rows are reconciled by `_flag_orphan_ok_rows` on backend startup

- GIVEN the operator deletes a parquet file for figi `BBG011MSLF25`
  while the backend is stopped
- WHEN the backend restarts and `_flag_orphan_ok_rows` runs in
  `lifespan`
- THEN the stale `instrument_metadata` row for that figi is set to
  `total_bars = 0` and `last_run_status = 'bars_missing_on_disk'`
- AND `/api/admin/backfill/pending` reports that figi as needing a
  re-fetch
- AND a subsequent backfill overwrites the stale row with fresh
  counts once the figi is processed again
