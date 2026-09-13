-- apps/api/src/algotrader_api/db/migrations/009_corporate_actions_source.sql
-- Adds `source` provenance column to corporate_actions so we know which
-- ingestion path produced each row (moex_iss, tinkoff, curated, moex_iss_snapshots).
--
-- The column is NULL-able because existing rows were written before this
-- migration; the one-shot backfill script migrate_corporate_actions_source.py
-- populates them from the legacy `note` prefix.
--
-- Re-running this migration on an already-migrated DB is a no-op:
-- run_migrations() catches "duplicate column" errors per its idempotency
-- contract.

ALTER TABLE corporate_actions ADD COLUMN source TEXT;

CREATE INDEX IF NOT EXISTS idx_corporate_actions_source ON corporate_actions(source);