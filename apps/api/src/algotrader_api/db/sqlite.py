"""SQLite connection with WAL mode and OTel instrumentation."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..observability.instrumentation import instrument_db_query

_lock = threading.Lock()
_connections: dict[str, sqlite3.Connection] = {}


def get_connection(path: str) -> sqlite3.Connection:
    """Get or create a SQLite connection at `path`. Enables WAL mode on first access."""
    with _lock:
        if path in _connections:
            return _connections[path]

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _connections[path] = conn
        return conn


def close_all() -> None:
    with _lock:
        for conn in _connections.values():
            try:
                conn.close()
            except Exception:
                pass
        _connections.clear()


def execute(
    path: str,
    sql: str,
    params: tuple[Any, ...] | None = None,
) -> list[sqlite3.Row]:
    """Execute SQL with OTel span instrumentation. Supports multi-statement via executescript."""
    with instrument_db_query(db_system="sqlite", statement=sql) as span:
        conn = get_connection(path)
        # Multi-statement detection: contains semicolons beyond trailing whitespace.
        # For schema migrations use executescript which commits implicitly; for SELECT
        # use parameterized execute.
        if params is None and ";" in sql.rstrip().rstrip(";"):
            conn.executescript(sql)
            span.set_attribute("db.row_count", -1)
            return []
        cur = conn.execute(sql, params or ())
        rows = cur.fetchall()
        span.set_attribute("db.row_count", len(rows))
        conn.commit()
        return rows


def execute_returning_id(
    path: str,
    sql: str,
    params: tuple[Any, ...] | None = None,
) -> int:
    """Execute INSERT and return lastrowid."""
    with instrument_db_query(db_system="sqlite", statement=sql) as span:
        conn = get_connection(path)
        cur = conn.execute(sql, params or ())
        conn.commit()
        rid = cur.lastrowid or 0
        span.set_attribute("db.row_count", 1)
        span.set_attribute("db.row_id", rid)
        return rid


def run_migrations(path: str, migrations_dir: str) -> None:
    """Apply SQL migration files. Delegates to migrations_runner."""
    from .migrations_runner import run_migrations as _runner_run_migrations

    return _runner_run_migrations(path, migrations_dir)


def _seed_moex_holidays_if_missing(conn: sqlite3.Connection, db_path: str) -> None:
    """Issue #4: seed the 2020-2027 MOEX holiday calendar on fresh install.

    The table itself is created by migration 006. This helper only
    inserts the rows when the table is present but empty. Idempotent:
    if the operator has already populated it via the historical
    import script (``import_moex_holidays.py``) the table is
    non-empty and we skip.

    The ``OperationalError`` branch handles pre-migration-006 databases
    (the table simply does not exist yet).
    """
    try:
        row = conn.execute("SELECT COUNT(*) FROM moex_holidays").fetchone()
    except sqlite3.OperationalError:
        return
    if row[0] > 0:
        return
    try:
        from ..scripts_import.import_moex_holidays import import_moex_holidays
    except Exception:  # noqa: BLE001 — missing JSON or broken import path is non-fatal
        return
    import_moex_holidays(db_path)



