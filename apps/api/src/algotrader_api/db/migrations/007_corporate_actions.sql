-- apps/api/src/algotrader_api/db/migrations/007_corporate_actions.sql
-- Corporate actions (splits + dividends) used to adjust historical bars
-- and to compute INCOMPLETE_HISTORY health signals. One row per event;
-- PK enforces idempotent re-runs via INSERT OR REPLACE.

CREATE TABLE IF NOT EXISTS corporate_actions (
    figi         TEXT NOT NULL,
    action_type  TEXT NOT NULL CHECK (action_type IN ('split', 'dividend')),
    ex_date      DATE NOT NULL,
    factor       REAL NOT NULL,
    cash_amount  REAL,
    note         TEXT,
    PRIMARY KEY (figi, action_type, ex_date)
);

CREATE INDEX IF NOT EXISTS idx_corporate_actions_figi ON corporate_actions(figi);
CREATE INDEX IF NOT EXISTS idx_corporate_actions_ex_date ON corporate_actions(ex_date);
