-- PR #129 (2026-09-24): Tinkoff fallback circuit breaker.
--
-- Problem: empty figis (no MOEX board, no bars ever written) trigger
-- Tinkoff fallback timeout (45s) on every backfill cycle. With 714
-- such figis, the cycle takes ~9 hours just waiting on Tinkoff to
-- refuse data it never had. Bars never grow because the cycle never
-- finishes; downstream phases (corporate_actions, dividends) never run.
--
-- Fix: persist a circuit breaker in instrument_metadata. After
-- ``TINKOFF_BREAKER_THRESHOLD`` consecutive empty Tinkoff responses
-- (timeout OR no-data) within ``TINKOFF_BREAKER_WINDOW_HOURS``,
-- the figi is marked ``tinkoff_breaker_open=1`` with a
-- ``tinkoff_breaker_open_until`` timestamp. ``_process_tinkoff``
-- short-circuits with a single log line instead of issuing the
-- 45s Tinkoff call. After 24h the breaker auto-resets and we try
-- again — in case Tinkoff data finally landed or a new instrument
-- arrived.
--
-- Forward-only: ALTER TABLE adds nullable columns, default values are
-- safe for already-running workers.

ALTER TABLE instrument_metadata ADD COLUMN tinkoff_breaker_open INTEGER NOT NULL DEFAULT 0;
ALTER TABLE instrument_metadata ADD COLUMN tinkoff_breaker_open_until TEXT;
ALTER TABLE instrument_metadata ADD COLUMN tinkoff_consecutive_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE instrument_metadata ADD COLUMN tinkoff_last_failure_ts TEXT;

-- Index for the common "is the breaker open for this figi?" lookup
-- inside _process_tinkoff, called once per figi per cycle.
CREATE INDEX IF NOT EXISTS instrument_metadata_breaker_idx
    ON instrument_metadata (figi, tinkoff_breaker_open, tinkoff_breaker_open_until);
