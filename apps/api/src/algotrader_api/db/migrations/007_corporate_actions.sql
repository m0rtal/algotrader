-- apps/api/src/algotrader_api/db/migrations/007_corporate_actions.sql
-- Corporate actions table: per-figi stock splits and dividend events.
-- One row per discrete event. factor is the cumulative adjustment factor
-- applied backward to all bars with ts < ex_date (Chicago Booth convention).
-- cash_amount is the per-share dividend (NULL for splits).
-- Loading is via scripts/import_corporate_actions.py against the static
-- JSON in apps/api/scripts/data/corporate_actions.json.

CREATE TABLE IF NOT EXISTS corporate_actions (
    figi        TEXT    NOT NULL,
    action_type TEXT    NOT NULL CHECK (action_type IN ('split', 'dividend')),
    ex_date     DATE    NOT NULL,
    factor      REAL    NOT NULL,
    cash_amount REAL,
    note        TEXT,
    PRIMARY KEY (figi, action_type, ex_date)
);

CREATE INDEX IF NOT EXISTS idx_corporate_actions_figi
    ON corporate_actions(figi, ex_date);
