-- Single-flight lock for the daily guardian (issue #5).
--
-- Without this table, two parallel systemd timer invocations of
-- ``run_daily_guardian`` both pass through ``compute_all`` and
-- ``recover_stale``, both INSERT a ``guardian_daily`` pipeline row,
-- and both race on the ``_mark_*_exhausted`` UPDATEs.
--
-- The orchestrator acquires a single-row sentinel via
-- ``INSERT INTO guardian_lock ... ON CONFLICT DO NOTHING`` inside
-- a ``BEGIN IMMEDIATE`` transaction. The second concurrent worker
-- fails the INSERT, sees no row, and raises ``GuardianLocked``.
--
-- The lock row is keyed on a constant id so the table always holds
-- at most one row. ``started_at`` records when the holder grabbed
-- the lock so operators can see when the current run began.
CREATE TABLE IF NOT EXISTS guardian_lock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    holder_pid INTEGER,
    started_at TEXT NOT NULL DEFAULT (datetime('now'))
);
