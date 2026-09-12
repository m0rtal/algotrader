-- Raw OHLCV bars table for the drilldown endpoint and the
-- operator-facing pending counter. Bars are written by
-- `BackfillRunner._backfill_one` for every successful fetch in the
-- same SQLite transaction that updates `instrument_metadata`, so
-- the row count never drifts away from the metadata. The table is
-- the canonical source for `/api/bars/<symbol>`; parquet files
-- remain on disk as the raw historical archive but no longer gate
-- the read path.

CREATE TABLE IF NOT EXISTS bars (
    figi    TEXT NOT NULL,
    ts      DATE NOT NULL,
    open    REAL NOT NULL,
    high    REAL NOT NULL,
    low     REAL NOT NULL,
    close   REAL NOT NULL,
    volume  INTEGER,
    PRIMARY KEY (figi, ts)
);

CREATE INDEX IF NOT EXISTS idx_bars_figi ON bars(figi);

-- The `instrument_metadata` table now also records the first-bar
-- timestamp alongside the last. This lets the Bars tab header
-- display the date range without scanning the bars table for the
-- min ts. ALTER TABLE is idempotent in the sense that re-running
-- this migration on a fresh database is fine (column is added once);
-- re-running on an already-migrated database raises an "duplicate
-- column" error which the migration runner treats as no-op.
ALTER TABLE instrument_metadata ADD COLUMN first_bar_ts TEXT;
