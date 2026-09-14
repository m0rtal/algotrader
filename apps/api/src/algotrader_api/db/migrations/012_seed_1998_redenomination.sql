-- 012_seed_1998_redenomination.sql
-- On 1998-01-01 Russia redenominated the ruble at 1000:1. Every
-- pre-1998 share price must be divided by 1000 for backward
-- adjustment. We insert this as a synthetic split so the
-- bars_adjusted view produces correct adj_close for any figi
-- that traded before 1998-01-01.
--
-- Source: Central Bank of Russia announcement 1997-08-04 (law
-- 110-FZ). Cross-checked with MOEX historical price archive.
--
-- Only figis with first_bar_ts < 1998-01-01 receive the split.
-- YNDX (IPO 2013) and other post-1998 listings are excluded.
--
-- Self-contained: the corporate_actions table is created here
-- with CREATE TABLE IF NOT EXISTS so this migration can run on
-- fresh databases where the table hasn't been created elsewhere.

CREATE TABLE IF NOT EXISTS corporate_actions (
    figi        TEXT    NOT NULL,
    action_type TEXT    NOT NULL CHECK (action_type IN ('split', 'dividend')),
    ex_date     DATE    NOT NULL,
    factor      REAL    NOT NULL,
    cash_amount REAL,
    note        TEXT,
    source      TEXT,
    PRIMARY KEY (figi, action_type, ex_date)
);

INSERT OR IGNORE INTO corporate_actions
    (figi, action_type, ex_date, factor, cash_amount, note, source)
SELECT DISTINCT
    i.figi,
    'split',
    '1998-01-01',
    1000.0,
    0.0,
    '1998 ruble redenomination (Federal Law 110-FZ)',
    'manual:moex_announcements:cbr_redenomination_1998'
FROM instruments i
JOIN bars b ON b.figi = i.figi
WHERE b.ts < '1998-01-01';
