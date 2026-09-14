-- 010_restricted_periods.sql
-- Table of dates on which MOEX trading was suspended, restricted,
-- or otherwise non-representative. The data-quality health check
-- excludes these dates from "missing bar" calculations so that
-- gaps during restricted periods do not inflate the stale count.

CREATE TABLE IF NOT EXISTS restricted_periods (
    date    TEXT PRIMARY KEY,        -- ISO 8601 YYYY-MM-DD
    reason  TEXT NOT NULL,           -- human-readable explanation
    source  TEXT NOT NULL DEFAULT 'manual:moex_announcements'
);
