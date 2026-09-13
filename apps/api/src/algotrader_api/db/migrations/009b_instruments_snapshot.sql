-- apps/api/src/algotrader_api/db/migrations/009b_instruments_snapshot.sql
-- Per-secid snapshots of MOEX ISS instrument metadata (face_value, lot_size)
-- used by the split detector in import_corporate_actions_splits.detect_mode().
--
-- A new row is appended on every cron run; the detector compares consecutive
-- snapshots per secid to find face_value changes and emits a
-- CorporateActionRow(action_type='split', factor=new/prev).
--
-- PK is (secid, observed_at) so re-running the same observed_at is a no-op
-- (INSERT OR IGNORE) and ordering by observed_at per secid gives the
-- chronological chain.

CREATE TABLE IF NOT EXISTS instruments_snapshot (
    secid        TEXT NOT NULL,
    observed_at  TEXT NOT NULL,           -- ISO datetime (UTC) of the cron run
    face_value   REAL,                    -- nominal per share, in RUB
    lot_size     INTEGER,                 -- MOEX standard lot for the security
    PRIMARY KEY (secid, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_instruments_snapshot_secid ON instruments_snapshot(secid);
CREATE INDEX IF NOT EXISTS idx_instruments_snapshot_observed ON instruments_snapshot(observed_at);