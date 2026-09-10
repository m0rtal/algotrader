"""Secret storage backed by the application SQLite database.

Single-row key/value table (`secrets`) for tokens and other credentials that
don't belong in the structured settings JSON (which is user-visible via the
Settings UI). Lives in the same SQLite WAL database as settings/pipeline —
so the secret file is no longer separate from the rest of app state.

The token never leaves the server. UI shows only last-4 chars.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .sqlite import execute

BROKER_TOKEN_KEY = "broker_token"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_secret(sqlite_path: str, key: str) -> str | None:
    """Return the secret value for `key`, or None if not set."""
    rows = execute(sqlite_path, "SELECT value FROM secrets WHERE key = ?", (key,))
    return rows[0]["value"] if rows else None


def set_secret(sqlite_path: str, key: str, value: str) -> None:
    """Insert or update a secret. Stores an updated_at timestamp.

    Logs the write to `ingestion_logs` (operator-visible event stream)
    with last-4 only — full value never travels through the log.
    """
    execute(
        sqlite_path,
        "INSERT INTO secrets (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, _now_iso()),
    )
    try:
        # Best-effort audit trail. The ingestion_logs schema is created
        # by an early migration; on a fresh test DB this table may not
        # exist yet, so swallow the failure.
        execute(
            sqlite_path,
            "INSERT INTO ingestion_logs (ts, run_id, level, figi, message) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                _now_iso(),
                0,
                "info",
                "",
                f"secrets.write key={key} last4={value[-4:]}",
            ),
        )
    except Exception:
        pass


def get_broker_token(sqlite_path: str) -> str | None:
    """Convenience: returns the broker token or None."""
    val = get_secret(sqlite_path, BROKER_TOKEN_KEY)
    return val if val else None
