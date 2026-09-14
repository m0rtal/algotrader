-- 014_dividends.sql
-- Tinkoff dividend events. Immutable, append-only: every (event, revision)
-- is a distinct row, identified by (figi, ex_date, period_year, period_no,
-- revision_n). A future board-AGM revision of the same dividend is recorded
-- as a new row with revision_n > 1 (see is_retroactive below); the original
-- row is never overwritten.
--
-- amount_per_share_rub is generated from amount_per_share and fx_rate_used
-- so the daily chain / freshness check can read RUB values without joining
-- a separate FX table.

CREATE TABLE IF NOT EXISTS dividends (
    figi                 TEXT    NOT NULL,
    ex_date              TEXT    NOT NULL,        -- YYYY-MM-DD
    pay_date             TEXT,                   -- nullable
    record_date          TEXT,
    declared_at          TEXT,                   -- ISO timestamp; nullable for old events
    period_year          INTEGER NOT NULL,        -- e.g. 2023 for "FY 2023 dividend"
    period_no            INTEGER NOT NULL DEFAULT 1,  -- 1 = annual, 2 = interim H1, etc.
    currency             TEXT    NOT NULL DEFAULT 'rub',
    amount_per_share     REAL    NOT NULL,
    fx_rate_used         REAL,                    -- NULL for RUB (rate implicit 1.0)
    dividend_type        TEXT    NOT NULL DEFAULT 'regular',
    regularity           TEXT,                    -- 'Annual' / 'SemiAnnual' / ...
    close_price          REAL,
    yield_value          REAL,
    yield_pct            REAL,
    tax_withheld_pct     REAL,                    -- 13% residents / 15% non-residents; not filled by Tinkoff
    cancelled_at         TEXT,                    -- non-null = AGM reversed the dividend
    source               TEXT    NOT NULL DEFAULT 'tinkoff',
    source_revision_ts   TEXT,                    -- broker's created_at if any
    retrieved_at         TEXT    NOT NULL,        -- our fetch timestamp
    revision_n           INTEGER NOT NULL DEFAULT 1,
    is_retroactive       INTEGER GENERATED ALWAYS AS (CASE WHEN revision_n > 1 THEN 1 ELSE 0 END) STORED,
    amount_per_share_rub REAL    GENERATED ALWAYS AS (
        CASE WHEN fx_rate_used IS NULL THEN amount_per_share
             ELSE amount_per_share * fx_rate_used END
    ) STORED,
    note                 TEXT,
    PRIMARY KEY (figi, ex_date, period_year, period_no, revision_n)
);

CREATE INDEX IF NOT EXISTS idx_dividends_figi_ex
    ON dividends(figi, ex_date);
CREATE INDEX IF NOT EXISTS idx_dividends_ex_date
    ON dividends(ex_date);
