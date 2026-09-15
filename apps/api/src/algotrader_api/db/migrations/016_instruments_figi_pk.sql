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
-- Migration uses SQLite's table-rebuild dance (PRAGMA foreign_keys=OFF
-- because this migration does not touch FK constraints).

PRAGMA foreign_keys=OFF;

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

PRAGMA foreign_keys=ON;
