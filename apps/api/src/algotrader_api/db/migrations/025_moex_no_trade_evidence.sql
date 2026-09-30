-- apps/api/src/algotrader_api/db/migrations/025_moex_no_trade_evidence.sql
--
-- Per-figi, per-session-date evidence that MOEX ISS returned an
-- explicit zero-trade row for the issuer's primary tradable board on
-- that date. Recorded ONLY when the upstream response was a full,
-- successful, complete-paginated, NULL-OHLC row matching the figi's
-- SECID and BOARDID plus ISIN. Empty / error / partial / wrong-board
-- responses do NOT produce evidence (they stay "unknown").
--
-- Each row is independent of `bars`: a real bar that ever lands for
-- (figi, session_date) must take precedence over a stored evidence
-- row (the gate treats the bar as the truth; the evidence is then a
-- stale artefact that the next evidence refresh overwrites or the
-- reconciliation query surfaces).

CREATE TABLE IF NOT EXISTS moex_no_trade_evidence (
    figi            TEXT    NOT NULL,
    session_date    TEXT    NOT NULL,                    -- ISO date string
    board           TEXT    NOT NULL,                    -- MOEX BOARDID
    isin            TEXT    NOT NULL,                    -- MOEX description ISIN
    source          TEXT    NOT NULL DEFAULT 'moex_iss', -- upstream source label
    observed_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at      TEXT    NOT NULL,                    -- re-validation deadline
    UNIQUE (figi, session_date)
);

CREATE INDEX IF NOT EXISTS idx_moex_no_trade_evidence_figi
    ON moex_no_trade_evidence (figi);

CREATE INDEX IF NOT EXISTS idx_moex_no_trade_evidence_expiry
    ON moex_no_trade_evidence (expires_at);
