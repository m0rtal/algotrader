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
    """Apply SQL migration files in lexical order. Idempotent.

    Individual statements use `CREATE TABLE IF NOT EXISTS` /
    `CREATE INDEX IF NOT EXISTS` so they no-op on re-run. `ALTER TABLE
    ADD COLUMN` is the one statement that isn't idempotent — we
    skip "duplicate column" errors so the operator can safely rerun
    the migration suite on an already-initialised database.

    Note: we bypass the OTel-instrumented `execute()` helper here on
    purpose — `execute()` uses `executescript` which aborts on the
    first error and we need per-statement fault tolerance.
    """
    migrations_path = Path(migrations_dir)
    if not migrations_path.exists():
        return
    files = sorted(migrations_path.glob("*.sql"))
    conn = get_connection(path)
    for f in files:
        sql = f.read_text(encoding="utf-8")
        for stmt in _split_statements(sql):
            try:
                conn.executescript(stmt)
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    continue  # idempotent no-op for ADD COLUMN / CREATE
                raise
    conn.commit()


def _split_statements(sql: str) -> list[str]:
    """Split a multi-statement migration file into individual statements.

    Strips pure-comment statements so they don't trigger execution.
    """
    out: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        buf.append(line)
        stripped = line.strip()
        if stripped.endswith(";"):
            chunk = "\n".join(buf).strip()
            buf.clear()
            # Skip pure-comment statements.
            lines = [l for l in chunk.splitlines() if l.strip()]
            if lines and all(l.strip().startswith("--") for l in lines):
                continue
            if chunk:
                out.append(chunk)
    tail = "\n".join(buf).strip()
    if tail:
        lines = [l for l in tail.splitlines() if l.strip()]
        if not (lines and all(l.strip().startswith("--") for l in lines)):
            out.append(tail)
    return out
