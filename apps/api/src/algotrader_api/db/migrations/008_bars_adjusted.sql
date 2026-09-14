-- apps/api/src/algotrader_api/db/migrations/008_bars_adjusted.sql
-- Backward-adjusted close view. For each bar, computes the cumulative
-- split factor from all splits on or after the bar's ts, and divides
-- close by that factor. Chicago Booth-style adjustment (backward):
-- historical pre-split prices get scaled DOWN to match post-split scale.
-- Dividend events are recorded but not folded into the price (the
-- total-return / dividend-reinvested variant is out of scope here).
--
-- Portable cumulative product: `EXP(SUM(LN(factor)))` works on every
-- SQLite version the project ships; `PRODUCT()` was added in 3.35 and
-- is avoided here for compatibility.

CREATE VIEW IF NOT EXISTS bars_adjusted AS
SELECT
    b.figi,
    b.ts,
    b.open,
    b.high,
    b.low,
    b.close,
    b.volume,
    CASE
        WHEN (
            SELECT COALESCE(EXP(SUM(LN(sf.factor))), 1.0)
            FROM corporate_actions sf
            WHERE sf.figi = b.figi
              AND sf.action_type = 'split'
              AND sf.ex_date > b.ts
        ) = 0 THEN b.close
        ELSE b.close / (
            SELECT COALESCE(EXP(SUM(LN(sf.factor))), 1.0)
            FROM corporate_actions sf
            WHERE sf.figi = b.figi
              AND sf.action_type = 'split'
              AND sf.ex_date > b.ts
        )
    END AS adj_close,
    (
        SELECT GROUP_CONCAT(sf.factor, 'x')
        FROM corporate_actions sf
        WHERE sf.figi = b.figi
          AND sf.action_type = 'split'
          AND sf.ex_date > b.ts
    ) AS adj_factors_applied
FROM bars b;
