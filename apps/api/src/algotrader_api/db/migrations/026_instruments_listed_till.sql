-- apps/api/src/algotrader_api/db/migrations/026_instruments_listed_till.sql
--
-- Per-instrument MOEX delisting date. Populated by
-- apps/api/scripts/backfill_no_trade_evidence.py for instruments that
-- are no longer traded on MOEX (all boards have is_traded=0 and the
-- latest listed_till is in the past).
--
-- The ML coverage gate uses this as the effective end of the expected
-- sessions: a delisted instrument is complete when it has bars (or
-- confirmed no-trade evidence) up to its listed_till, and must not be
-- flagged stale for sessions after the delisting.
--
-- NULL means "still listed / unknown" and preserves the old behaviour.

ALTER TABLE instruments ADD COLUMN listed_till TEXT;
