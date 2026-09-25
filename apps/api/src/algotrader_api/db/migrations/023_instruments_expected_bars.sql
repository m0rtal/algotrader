-- apps/api/src/algotrader_api/db/migrations/023_instruments_expected_bars.sql
-- Cache expected bar count per figi (used by features.build_features gate
-- to compute 95% completeness threshold without per-call holiday arithmetic).
ALTER TABLE instruments ADD COLUMN expected_bars INTEGER;
