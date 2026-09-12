-- Add `first_bar_ts` to `instrument_metadata` so the Bars tab header
-- can render date range without scanning the bars table.
--
-- Migration is idempotent: re-running on a database that already
-- has the column is a no-op. We use `PRAGMA table_info` to detect
-- presence and only issue ALTER when the column is missing.

ALTER TABLE instrument_metadata ADD COLUMN first_bar_ts TEXT;
