-- ml-data-readiness PR / dividends-throttling (2026-09-24):
-- Persistent retry queue for figis whose `GetDividends` returned
-- `RESOURCE_EXHAUSTED` (Tinkoff 200/min rate cap). Each cycle the
-- chain drains this queue first; convergence in O(cap_size) cycles
-- regardless of how many figis the upstream rejects in one pass.
--
-- Why a table and not a per-process deque: the derived worker can
-- be killed (e.g. watchdog) between cycles; losing the queue would
-- re-burn the rate-limit budget on already-failed figis.

CREATE TABLE IF NOT EXISTS dividends_throttle_pending (
    figi             TEXT PRIMARY KEY,
    first_failed_at  TEXT NOT NULL,
    last_failed_at   TEXT NOT NULL,
    retry_count      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_div_pending_last_failed
    ON dividends_throttle_pending(last_failed_at);
