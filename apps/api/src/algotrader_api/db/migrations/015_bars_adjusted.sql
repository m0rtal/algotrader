-- Forward-adjustment bars. Replaces the legacy backward-adjustment VIEW
-- with a TABLE that is precomputed when a split lands (see
-- data_quality/forward_adjustment.py).
DROP VIEW IF EXISTS bars_adjusted;
CREATE TABLE bars_adjusted (
    figi        TEXT    NOT NULL,
    ts          TEXT    NOT NULL,                 -- YYYY-MM-DD
    adj_open    REAL    NOT NULL,
    adj_high    REAL    NOT NULL,
    adj_low     REAL    NOT NULL,
    adj_close   REAL    NOT NULL,
    adj_volume  INTEGER NOT NULL,                 -- volume NOT adjusted (splits don't change volume)
    source      TEXT    NOT NULL DEFAULT 'derived:bars+forward',
    computed_at TEXT    NOT NULL,                 -- ISO timestamp
    PRIMARY KEY (figi, ts)
);
CREATE INDEX IF NOT EXISTS idx_bars_adjusted_figi_ts
    ON bars_adjusted(figi, ts);
