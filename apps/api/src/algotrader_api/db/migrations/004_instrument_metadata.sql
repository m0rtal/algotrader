-- Backfill subsystem state + operator-visible logs.
--
-- Two new tables added on top of 001_init, 002_instruments_and_pipeline,
-- 003_secrets. Backfill writes per-ticker progress to instrument_metadata;
-- every operator-visible event lands in ingestion_logs.
--
-- Both tables are forward-only and idempotent: re-running this migration
-- on a populated database is a no-op.

CREATE TABLE IF NOT EXISTS instrument_metadata (
    figi              TEXT PRIMARY KEY,
    last_bar_ts       TEXT,                                  -- ISO date or NULL if never written
    last_backfilled_at TEXT,                                 -- ISO datetime of last successful backfill
    total_bars        INTEGER NOT NULL DEFAULT 0,
    last_run_status   TEXT,                                  -- 'ok' | 'partial' | 'error' | 'skipped'
    last_run_at       TEXT,                                  -- ISO datetime of last attempted run
    last_error        TEXT                                   -- short error message if status='error'
);

CREATE TABLE IF NOT EXISTS ingestion_logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,                                  -- ISO datetime
    run_id    INTEGER NOT NULL,
    level     TEXT NOT NULL,                                  -- 'debug' | 'info' | 'warn' | 'error'
    figi      TEXT,                                           -- nullable; non-ticker logs (start/stop/rate limit) leave this NULL
    message   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ingestion_logs_ts_idx
    ON ingestion_logs (ts DESC);

CREATE INDEX IF NOT EXISTS ingestion_logs_run_idx
    ON ingestion_logs (run_id, ts DESC);
