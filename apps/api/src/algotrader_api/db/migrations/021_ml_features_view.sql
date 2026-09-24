-- ml-data-readiness PR-1 (2026-09-24): ML-ready long panel.
-- One row per (figi, ts) with raw + forward-adjusted close,
-- trailing-12m dividend sum, and a tradeable filter.
-- Idempotent: DROP IF EXISTS + CREATE, safe across re-runs.

DROP VIEW IF EXISTS ml_features;
CREATE VIEW ml_features AS
SELECT
    b.figi                                              AS figi,
    b.ts                                                AS ts,
    b.open                                              AS open,
    b.high                                              AS high,
    b.low                                               AS low,
    b.close                                             AS close,
    b.volume                                            AS volume,
    b.source                                            AS source,

    -- Cumulative split factor at ts: product of all splits whose
    -- ex_date <= ts. 1.0 when no splits have landed yet.
    COALESCE((
        SELECT 1.0 * EXP(SUM(LN(ca.factor)))
        FROM corporate_actions ca
        WHERE ca.figi = b.figi AND ca.ex_date <= b.ts
    ), 1.0)                                             AS cumulative_split_factor,

    -- Forward-adjusted close: raw divided by cumulative factor.
    -- Chicago-Booth convention. Splits strictly AFTER ts do NOT
    -- retroactively affect this bar.
    b.close / COALESCE((
        SELECT 1.0 * EXP(SUM(LN(ca.factor)))
        FROM corporate_actions ca
        WHERE ca.figi = b.figi AND ca.ex_date <= b.ts
    ), 1.0)                                             AS adj_close,

    -- Trailing 12 months of dividends per share (RUB). NULL when no
    -- rows in window. NULL = unknown; 0 would mean "we know there's
    -- no dividend", which the model can't distinguish from missing
    -- upstream data.
    (SELECT SUM(d.amount_per_share_rub)
        FROM dividends d
        WHERE d.figi = b.figi
          AND d.ex_date > date(b.ts, '-12 months')
          AND d.ex_date <= b.ts)                         AS dividend_paid_ttm_rub,

    -- is_tradeable = the figi is in instruments with class in
    -- (share, etf, bond) and figi is non-null. Used by the model
    -- to drop delisted / placeholder rows automatically.
    EXISTS(SELECT 1 FROM instruments i
           WHERE i.figi = b.figi
             AND i.class IN ('share','etf','bond')
             AND i.figi IS NOT NULL)                     AS is_tradeable
FROM bars b;
