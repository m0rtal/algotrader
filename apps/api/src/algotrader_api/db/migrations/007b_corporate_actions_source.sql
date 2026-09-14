-- apps/api/src/algotrader_api/db/migrations/007b_corporate_actions_source.sql
-- Adds the `source` column to corporate_actions for traceability.
-- Runs between 007 (initial table without source) and 012 (renomination
-- seed that INSERTs into `source`). Idempotent: ALTER ADD COLUMN raises
-- "duplicate column" on already-migrated DBs and run_migrations catches
-- it via its OperationalError handler.

ALTER TABLE corporate_actions ADD COLUMN source TEXT NOT NULL DEFAULT 'derived:bars+forward';
