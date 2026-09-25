"""Tests for the probe-and-branch idempotent migration 016.

Spec: openspec/changes/db-bootstrap-hardening/specs/data-quality/spec.md
(Requirement: Migration 016 Idempotent)
"""
import hashlib
import sqlite3
from pathlib import Path

import pytest

from algotrader_api.db.migrations_runner import MIGRATIONS_DIR, run_migrations


def _has_table(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _has_pk_on_ticker(con):
    rows = con.execute("PRAGMA table_info('instruments')").fetchall()
    # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk)
    for r in rows:
        if r[1] == "ticker" and r[5] > 0:  # pk column ordinal > 0
            return True
    return False


def _seed_schema_migrations_except_016(con):
    """Seed schema_migrations so the runner skips every file except
    016_instruments_figi_pk.sql. Used to isolate the cleanup branch
    (the runner's normal 001-015 flow re-creates `instruments` via
    migration 002, masking the interrupted state we want to test).
    """
    con.execute(
        """
        CREATE TABLE schema_migrations (
            migration_id  TEXT PRIMARY KEY,
            content_hash  TEXT NOT NULL,
            applied_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    migrations_dir = Path(MIGRATIONS_DIR)
    for f in sorted(migrations_dir.glob("*.sql")):
        if f.name == "016_instruments_figi_pk.sql":
            continue
        sql = f.read_text(encoding="utf-8")
        h = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        con.execute(
            "INSERT INTO schema_migrations (migration_id, content_hash) "
            "VALUES (?, ?)",
            (f.name, h),
        )


def test_migration_016_cleanup_branch(tmp_path):
    """instruments missing, instruments_new present → rename only,
    no rebuild dance.

    ADAPT-10: the brief's verbatim single-phase setup
    (just CREATE instruments_new, then run_migrations) cannot isolate
    the cleanup branch because migration 002
    (`instruments_and_pipeline.sql`) re-creates `instruments` with PK
    on ticker before 016's probe runs. The verbatim pre-state's PK on
    `instruments_new` would survive the rename, contradicting the
    post-state assertion `not _has_pk_on_ticker(con)`.

    Corrected setup: seed schema_migrations so the runner skips
    001-015 (and 018-022), DROP nothing, and CREATE `instruments_new`
    with 1 row (no PK — matching 016's in-progress CREATE TABLE in the
    rebuild dance). The single run_migrations call runs ONLY 016,
    which probes 'cleanup' and renames.
    """
    db = tmp_path / "test.db"
    con = sqlite3.connect(str(db))
    _seed_schema_migrations_except_016(con)
    con.executescript("""
        CREATE TABLE instruments_new (
            ticker TEXT NOT NULL,
            figi TEXT NOT NULL UNIQUE,
            class TEXT NOT NULL,
            name TEXT NOT NULL,
            currency TEXT NOT NULL,
            lot_size INTEGER NOT NULL,
            isin TEXT,
            sector TEXT,
            source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO instruments_new
            (ticker, figi, class, name, currency, lot_size)
        VALUES ('AAPL', 'BBG000000001', 'share', 'Apple', 'USD', 1);
    """)
    con.close()
    run_migrations(str(db), MIGRATIONS_DIR)
    con = sqlite3.connect(str(db))
    try:
        assert _has_table(con, "instruments")
        assert not _has_table(con, "instruments_new")
        n = con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
        assert n == 1
        assert not _has_pk_on_ticker(con)
    finally:
        con.close()


def test_migration_016_rebuild_branch(tmp_path):
    """instruments has PK on ticker → full rebuild dance."""
    db = tmp_path / "test.db"
    # Pre-state: instruments with PK on ticker (the pre-016 shape).
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE instruments (
            ticker TEXT PRIMARY KEY,
            figi TEXT NOT NULL UNIQUE,
            class TEXT NOT NULL,
            name TEXT NOT NULL,
            currency TEXT NOT NULL,
            lot_size INTEGER NOT NULL,
            isin TEXT,
            sector TEXT,
            source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO instruments
            (ticker, figi, class, name, currency, lot_size)
        VALUES ('AAPL', 'BBG000000001', 'share', 'Apple', 'USD', 1),
               ('MSFT', 'BBG000000002', 'share', 'Microsoft', 'USD', 1);
    """)
    con.close()
    run_migrations(str(db), MIGRATIONS_DIR)
    # Post-state: PK on ticker is gone, all rows preserved.
    con = sqlite3.connect(str(db))
    try:
        assert _has_table(con, "instruments")
        assert not _has_table(con, "instruments_new")
        n = con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
        assert n == 2
        assert not _has_pk_on_ticker(con)
    finally:
        con.close()


def test_migration_016_noop_branch(tmp_path):
    """instruments has no PK on ticker (post-016 clean state) → do
    nothing, no schema drift."""
    db = tmp_path / "test.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE instruments (
            ticker TEXT NOT NULL,
            figi TEXT NOT NULL UNIQUE,
            class TEXT NOT NULL,
            name TEXT NOT NULL,
            currency TEXT NOT NULL,
            lot_size INTEGER NOT NULL,
            isin TEXT,
            sector TEXT,
            source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO instruments
            (ticker, figi, class, name, currency, lot_size)
        VALUES ('AAPL', 'BBG000000001', 'share', 'Apple', 'USD', 1);
    """)
    con.close()
    run_migrations(str(db), MIGRATIONS_DIR)
    # Post-state: row preserved, no schema change.
    con = sqlite3.connect(str(db))
    try:
        n = con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
        assert n == 1
        assert not _has_pk_on_ticker(con)
        assert not _has_table(con, "instruments_new")
    finally:
        con.close()


def test_migration_016_repeated_runs_no_state_change(tmp_path):
    """Apply migration 016 twice on the same DB → no schema drift,
    no row loss."""
    db = tmp_path / "test.db"
    # Pre-state: instruments with PK on ticker.
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE instruments (
            ticker TEXT PRIMARY KEY,
            figi TEXT NOT NULL UNIQUE,
            class TEXT NOT NULL,
            name TEXT NOT NULL,
            currency TEXT NOT NULL,
            lot_size INTEGER NOT NULL,
            isin TEXT,
            sector TEXT,
            source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO instruments
            (ticker, figi, class, name, currency, lot_size)
        VALUES ('AAPL', 'BBG000000001', 'share', 'Apple', 'USD', 1);
    """)
    con.close()
    run_migrations(str(db), MIGRATIONS_DIR)
    con = sqlite3.connect(str(db))
    n_after_first = con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
    con.close()
    run_migrations(str(db), MIGRATIONS_DIR)
    con = sqlite3.connect(str(db))
    try:
        n_after_second = con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
        assert n_after_first == n_after_second == 1
        assert not _has_table(con, "instruments_new")
    finally:
        con.close()