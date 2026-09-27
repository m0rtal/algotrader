-- apps/api/src/algotrader_api/db/migrations/024_pipeline_heartbeat_and_runs.sql
-- Autonomous Pipeline Liveness (autonomous-data-pipeline, Task 2):
--   1. pipeline_heartbeat — one row per tick of worker.py live mode's
--      daemon heartbeat thread. Read by the algotrader-supervisor
--      in-process watchdog AND by the new cron_liveness_check.sh
--      out-of-band watchdog. PRIMARY KEY on (worker_pid, phase) so
--      INSERT OR REPLACE from _heartbeat_loop() updates the existing
--      row instead of duplicating per tick.
--   2. pipeline_runs — one row per completed cycle of run_live_mode().
--      Drives the stale_2d_count field surfaced via
--      GET /api/admin/backfill/status (Task 3 — not yet wired).
--
-- Why this migration ships in Task 2 (not Task 1): Task 1's
-- run_live_mode() silently swallows "no such table" errors so the
-- live loop keeps ticking even without these tables; Task 2 makes
-- the writes observable and queryable. Removing the swallow path is
-- not in scope here — see Task 4 (full cleanup) in
-- openspec/.../tasks.md.

CREATE TABLE IF NOT EXISTS pipeline_heartbeat (
    worker_pid   INTEGER NOT NULL,
    phase        TEXT    NOT NULL,
    last_bar_ts  TEXT,
    updated_at   TEXT    NOT NULL,
    PRIMARY KEY (worker_pid, phase)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_heartbeat_updated
    ON pipeline_heartbeat(updated_at DESC);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT    NOT NULL,
    finished_at      TEXT    NOT NULL,
    rc               INTEGER NOT NULL,
    stale_2d_count   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started
    ON pipeline_runs(started_at DESC);
