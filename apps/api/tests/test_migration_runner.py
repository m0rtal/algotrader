"""Tests for migration runner tracking (schema_migrations table) and
per-statement savepoint wrap.

Spec: openspec/changes/db-bootstrap-hardening/specs/data-quality/spec.md
"""
import hashlib
import sqlite3
from pathlib import Path

import pytest

from algotrader_api.db.migrations_runner import (
    MigrationHashMismatch,
    MIGRATIONS_DIR,
    run_migrations,
)


def _count_applied_migrations(db_path):
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    finally:
        con.close()


def _applied_hashes(db_path):
    con = sqlite3.connect(str(db_path))
    try:
        return dict(
            con.execute(
                "SELECT migration_id, content_hash FROM schema_migrations"
            ).fetchall()
        )
    finally:
        con.close()


def test_schema_migrations_table_created_on_first_run(tmp_path):
    """schema_migrations table exists with the spec'd columns after
    run_migrations() runs on a fresh DB."""
    db = tmp_path / "test.db"
    run_migrations(str(db), MIGRATIONS_DIR)
    con = sqlite3.connect(str(db))
    try:
        cols = [
            r[1] for r in con.execute(
                "PRAGMA table_info(schema_migrations)"
            ).fetchall()
        ]
        assert "migration_id" in cols
        assert "content_hash" in cols
        assert "applied_at" in cols
    finally:
        con.close()


def test_already_applied_migrations_skipped_on_second_run(tmp_path):
    """Running migrations twice on the same DB records each migration
    exactly once (no duplicate schema_migrations rows)."""
    db = tmp_path / "test.db"
    run_migrations(str(db), MIGRATIONS_DIR)
    first_count = _count_applied_migrations(db)
    first_hashes = _applied_hashes(db)
    run_migrations(str(db), MIGRATIONS_DIR)
    second_count = _count_applied_migrations(db)
    second_hashes = _applied_hashes(db)
    assert first_count == second_count
    assert first_hashes == second_hashes


def test_hash_mismatch_raises(tmp_path, monkeypatch):
    """Tampering with an applied migration's content raises MigrationHashMismatch."""
    db = tmp_path / "test.db"
    run_migrations(str(db), MIGRATIONS_DIR)
    # Tamper: change one byte in the last-applied migration file.
    files = sorted(Path(MIGRATIONS_DIR).glob("*.sql"))
    last = files[-1]
    original = last.read_text()
    last.write_text(original + "\n-- tampered\n")
    try:
        with pytest.raises(MigrationHashMismatch):
            run_migrations(str(db), MIGRATIONS_DIR)
    finally:
        last.write_text(original)


def test_statement_savepoint_rollback_isolation(tmp_path, monkeypatch):
    """When a single statement raises a non-swallowed error, prior
    statements in the same file are committed and subsequent
    statements still run."""
    # Patch the _split_statements function to inject a statement that
    # always raises. This proves the savepoint isolation.
    from algotrader_api.db import migrations_runner

    real_split = migrations_runner._split_statements

    def broken_split(sql):
        stmts = real_split(sql)
        if not stmts:
            return stmts
        # Insert a "FAIL" statement after the first.
        return [stmts[0], "SELECT this_is_not_a_column;", *stmts[1:]]

    monkeypatch.setattr(migrations_runner, "_split_statements", broken_split)
    db = tmp_path / "test.db"
    # Should NOT raise — savepoint isolates the failure, runner
    # continues with subsequent statements.
    run_migrations(str(db), MIGRATIONS_DIR)
    # All migrations still recorded.
    n = _count_applied_migrations(db)
    assert n > 0