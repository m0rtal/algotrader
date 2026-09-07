-- Secret storage: key-value table for tokens that don't fit the structured
-- settings JSON (e.g. broker token, future API keys). Single-row keys.
CREATE TABLE IF NOT EXISTS secrets (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- The broker token is read by the ingestion worker; written by the
-- Settings UI via PUT /api/settings/token. Mode 0600-equivalent:
-- secrets table sits in the application data dir next to settings.db.
INSERT OR IGNORE INTO secrets (key, value)
VALUES ('broker_token', '');
