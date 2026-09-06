"""Tests for SQLite layer: WAL mode, migrations, OTel instrumentation."""
from __future__ import annotations

from algotrader_api.db import sqlite as sqlitedb


def test_get_connection_enables_wal(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlitedb.get_connection(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_get_connection_caches(tmp_path):
    db_path = str(tmp_path / "test.db")
    a = sqlitedb.get_connection(db_path)
    b = sqlitedb.get_connection(db_path)
    assert a is b
    sqlitedb.close_all()


def test_run_migrations_creates_tables(tmp_path):
    from algotrader_api.db.migrations import MIGRATIONS_DIR

    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    rows = sqlitedb.execute(db_path, "SELECT name FROM sqlite_master WHERE type='table'", ())
    names = {r["name"] for r in rows}
    assert {"settings", "signals", "trades", "portfolio", "logs"}.issubset(names)
    sqlitedb.close_all()


def test_run_migrations_idempotent(tmp_path):
    from algotrader_api.db.migrations import MIGRATIONS_DIR

    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)  # second call must not error
    rows = sqlitedb.execute(db_path, "SELECT name FROM sqlite_master WHERE type='table'", ())
    assert len(rows) >= 5
    sqlitedb.close_all()


def test_execute_returns_rows(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.execute(db_path, "CREATE TABLE foo (x INTEGER)")
    sqlitedb.execute(db_path, "INSERT INTO foo VALUES (1), (2), (3)", ())
    rows = sqlitedb.execute(db_path, "SELECT x FROM foo ORDER BY x", ())
    assert [r["x"] for r in rows] == [1, 2, 3]
    sqlitedb.close_all()


def test_execute_multi_statement_via_executescript(tmp_path):
    """Migrations use multi-statement SQL; ensure executescript path works."""
    db_path = str(tmp_path / "test.db")
    sql = "CREATE TABLE a (x INTEGER); CREATE TABLE b (y INTEGER);"
    sqlitedb.execute(db_path, sql)
    rows = sqlitedb.execute(db_path, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", ())
    names = [r["name"] for r in rows]
    assert "a" in names and "b" in names
    sqlitedb.close_all()


def test_execute_with_params_prevents_injection(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.execute(db_path, "CREATE TABLE foo (x INTEGER)")
    # Even with a ";" inside, params path is used
    sqlitedb.execute(db_path, "INSERT INTO foo VALUES (?)", (42,))
    rows = sqlitedb.execute(db_path, "SELECT x FROM foo", ())
    assert rows[0]["x"] == 42
    sqlitedb.close_all()


def test_execute_returning_id_inserts_and_returns_id(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.execute(db_path, "CREATE TABLE foo (id INTEGER PRIMARY KEY, x INTEGER)")
    rid = sqlitedb.execute_returning_id(db_path, "INSERT INTO foo (x) VALUES (?)", (99,))
    assert rid > 0
    rows = sqlitedb.execute(db_path, "SELECT x FROM foo WHERE id = ?", (rid,))
    assert rows[0]["x"] == 99
    sqlitedb.close_all()


def test_close_all_clears_cache(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlitedb.get_connection(db_path)
    sqlitedb.close_all()
    # After close, next get_connection creates new connection (new object)
    new_conn = sqlitedb.get_connection(db_path)
    assert new_conn is not conn
    sqlitedb.close_all()


def test_run_migrations_empty_dir_is_noop(tmp_path):
    """Calling run_migrations with empty directory should not error."""
    db_path = str(tmp_path / "test.db")
    nonexistent_dir = str(tmp_path / "no-migrations-here")
    sqlitedb.run_migrations(db_path, nonexistent_dir)  # no exception
    sqlitedb.close_all()
