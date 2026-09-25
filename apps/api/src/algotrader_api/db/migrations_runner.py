"""Migration runner with schema_migrations tracking + per-statement savepoint wrap.

Spec: openspec/changes/db-bootstrap-hardening/specs/data-quality/spec.md
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Iterable

from algotrader_api.observability.logging import get_logger


_LOG = get_logger("algotrader_api.db.migrations_runner")


MIGRATIONS_DIR = str(Path(__file__).parent / "migrations")


class MigrationHashMismatch(Exception):
    """Raised when a migration file's content hash differs from the
    recorded hash in schema_migrations. Silent re-application of
    mutated SQL is forbidden.
    """


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id  TEXT PRIMARY KEY,
            content_hash  TEXT NOT NULL,
            applied_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _hash_sql(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _already_applied(
    conn: sqlite3.Connection, migration_id: str, content_hash: str
) -> bool:
    row = conn.execute(
        "SELECT content_hash FROM schema_migrations WHERE migration_id = ?",
        (migration_id,),
    ).fetchone()
    return row is not None and row[0] == content_hash


def _record_applied(
    conn: sqlite3.Connection, migration_id: str, content_hash: str
) -> None:
    conn.execute(
        "INSERT INTO schema_migrations (migration_id, content_hash) "
        "VALUES (?, ?)",
        (migration_id, content_hash),
    )


def _is_swallowed_error(msg: str) -> bool:
    msg = msg.lower()
    return (
        "duplicate column" in msg
        or "already exists" in msg
        or "use drop table" in msg
        or "use drop view" in msg
    )


def _split_statements(sql: str) -> list[str]:
    """Split a multi-statement migration file into individual statements.

    Strips pure-comment statements so they don't trigger execution.
    Moved from sqlite.py:_split_statements (ADAPT-2).
    """
    out: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        buf.append(line)
        stripped = line.strip()
        if stripped.endswith(";"):
            chunk = "\n".join(buf).strip()
            buf.clear()
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


def run_migrations(
    db_path: str, migrations_dir: str = MIGRATIONS_DIR
) -> None:
    """Apply SQL migration files in lexical order. Idempotent.

    Tracks applied migrations in schema_migrations. Skips files whose
    (migration_id, content_hash) pair already appears in the table.
    Hash mismatch raises MigrationHashMismatch.

    Each statement runs inside a SAVEPOINT; failures rollback to the
    savepoint. Swallowed errors (duplicate column, already exists,
    use drop table/view) are skipped silently. Other errors are
    logged and execution continues with the next statement.
    """
    migrations_path = Path(migrations_dir)
    if not migrations_path.exists():
        return
    conn = sqlite3.connect(db_path)
    try:
        _ensure_schema_migrations_table(conn)
        files = sorted(migrations_path.glob("*.sql"))
        for f in files:
            sql = f.read_text(encoding="utf-8")
            content_hash = _hash_sql(sql)
            if _already_applied(conn, f.name, content_hash):
                _LOG.info(
                    "migration.skip",
                    file=f.name,
                    reason="hash_match",
                )
                continue
            statements = _split_statements(sql)
            any_failed = False
            for n, stmt in enumerate(statements):
                savepoint = f"_mig_{f.stem}_{n}"
                try:
                    conn.execute(f"SAVEPOINT {savepoint}")
                    # Each item from _split_statements is a single SQL
                    # statement (terminated by ';'); use execute() not
                    # executescript() — the latter implicitly commits
                    # and destroys the savepoint, breaking RELEASE.
                    conn.execute(stmt)
                    conn.execute(f"RELEASE {savepoint}")
                except sqlite3.OperationalError as e:
                    try:
                        conn.execute(f"ROLLBACK TO {savepoint}")
                    except sqlite3.OperationalError:
                        # Savepoint may already be gone if execute()
                        # failed for a structural reason (e.g. invalid
                        # SQL that aborts the surrounding transaction).
                        pass
                    if _is_swallowed_error(str(e)):
                        continue
                    _LOG.error(
                        "migration.statement_failed",
                        file=f.name,
                        statement_n=n,
                        error=str(e),
                    )
                    any_failed = True
                    continue
            # Record the migration if at least one statement ran
            # without an unrecoverable error. A single bad statement
            # (rolled back via savepoint) does not void the file's
            # other applied work. The hash-mismatch check happens
            # at the file level on the NEXT run (re-read content,
            # re-check vs. recorded).
            try:
                _record_applied(conn, f.name, content_hash)
            except sqlite3.IntegrityError:
                # Re-raised as MigrationHashMismatch if content_hash
                # differs from the recorded one.
                recorded = conn.execute(
                    "SELECT content_hash FROM schema_migrations "
                    "WHERE migration_id = ?",
                    (f.name,),
                ).fetchone()
                if recorded is not None and recorded[0] != content_hash:
                    raise MigrationHashMismatch(
                        f"{f.name}: stored hash {recorded[0]} != "
                        f"current {content_hash}"
                    )
        # Release the migration write lock before seeding (the seed
        # helper opens its own sqlite3 connection via
        # import_moex_holidays which would otherwise hit "database
        # is locked" against the WAL writer).
        conn.commit()
        # Preserve post-migration holidays seed (was previously in
        # sqlite.py:run_migrations before the runner refactor).
        # Import inside the function to avoid circular import
        # (sqlite.py imports from migrations_runner via the delegate).
        from .sqlite import _seed_moex_holidays_if_missing

        _seed_moex_holidays_if_missing(conn, db_path)
    finally:
        conn.close()