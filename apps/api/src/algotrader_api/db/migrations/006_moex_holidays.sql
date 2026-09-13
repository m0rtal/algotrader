-- apps/api/src/algotrader_api/db/migrations/006_moex_holidays.sql
CREATE TABLE IF NOT EXISTS moex_holidays (
    date TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_moex_holidays_date ON moex_holidays(date);
