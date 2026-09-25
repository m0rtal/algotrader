-- apps/api/src/algotrader_api/db/migrations/016_instruments_figi_pk.sql
-- Drop PRIMARY KEY on `instruments.ticker` so two figis sharing a
-- ticker (relisted instruments) can coexist as distinct rows. figi
-- remains UNIQUE so each figi is a distinct row.
--
-- Why: Tinkoff broker returns multiple figis for the same ticker when
-- instruments are relisted (e.g. TCS00A1057D4 → TCS90A1057D4 both
-- for bond RU000A1057D4). With ticker as PRIMARY KEY, the relisted
-- figi was silently dropped — losing 277+ bars of history in the
-- relisted figi. Dropping the PK preserves both rows; backfill and
-- bars queries address by figi, so duplicates are harmless.
--
-- 2026-09-25: made idempotent. The original migration was
-- non-idempotent because it always executed `CREATE TABLE
-- instruments_new` followed by `INSERT INTO instruments_new SELECT
-- FROM instruments`. On a clean database where `instruments`
-- already has no PRIMARY KEY on ticker (the migration's goal), the
-- CREATE+INSERT+DROP+RENAME dance re-runs every time. If anything
-- interrupts the sequence after DROP but before RENAME, the
-- database is left with `instruments_new` and NO `instruments` —
-- a state that the migration runner then perpetuates indefinitely.
--
-- The idempotent form below: probe state first, run the rebuild
-- dance only when needed, and do nothing when state is already
-- correct. Specifically:
--   1. If `instruments` does NOT exist AND `instruments_new` exists,
--      finish the interrupted rebuild by renaming only.
--   2. If `instruments` exists and has no PK on `ticker` (clean
--      state), do nothing.
--   3. If `instruments` exists and has a PK on `ticker` (first run),
--      run the full rebuild dance.

PRAGMA foreign_keys=OFF;

-- Probe: pick the branch based on current schema state.
DROP TABLE IF EXISTS _migration016_probe;
CREATE TEMP TABLE _migration016_probe (mode TEXT);
INSERT INTO _migration016_probe (mode)
SELECT CASE
  WHEN NOT EXISTS (SELECT 1 FROM sqlite_master WHERE type='table' AND name='instruments')
   AND EXISTS (SELECT 1 FROM sqlite_master WHERE type='table' AND name='instruments_new')
  THEN 'cleanup'
  WHEN EXISTS (SELECT 1 FROM sqlite_master WHERE type='table' AND name='instruments')
   AND EXISTS (
     SELECT 1 FROM pragma_table_info('instruments')
     WHERE "pk" > 0 AND "name" = 'ticker'
   )
  THEN 'rebuild'
  ELSE 'noop'
END;

-- Drop dependent views before any rename. Migration 021 will
-- re-create `ml_features` on the same migration pass.
DROP VIEW IF EXISTS ml_features;

-- Branch 1: cleanup — instruments missing, instruments_new present.
-- SAVEPOINT isolates the rename; if the source table is missing (the
-- probe got it wrong) the ALTER raises and we ROLLBACK TO, leaving
-- state unchanged.
SAVEPOINT _mig_016_cleanup;
ALTER TABLE instruments_new RENAME TO instruments;
RELEASE _mig_016_cleanup;

-- Branch 2: rebuild — instruments has PK on ticker.
-- Original table-rebuild dance.
CREATE TABLE instruments_new (
  ticker    VARCHAR NOT NULL,
  figi      VARCHAR NOT NULL UNIQUE,
  class     VARCHAR NOT NULL,
  name      VARCHAR NOT NULL,
  currency  VARCHAR NOT NULL,
  lot_size  INTEGER NOT NULL,
  isin      VARCHAR,
  sector    VARCHAR,
  source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO instruments_new
  (ticker, figi, class, name, currency, lot_size, isin, sector, source_updated_at)
  SELECT ticker, figi, class, name, currency, lot_size, isin, sector, source_updated_at
  FROM instruments;

DROP TABLE instruments;

ALTER TABLE instruments_new RENAME TO instruments;

CREATE INDEX IF NOT EXISTS idx_instruments_ticker ON instruments(ticker);
CREATE INDEX IF NOT EXISTS idx_instruments_class ON instruments(class);

DROP TABLE _migration016_probe;

PRAGMA foreign_keys=ON;