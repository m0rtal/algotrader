import sqlite3, time
from pathlib import Path
import pytest

from algotrader_api.db.sqlite import run_migrations
from algotrader_api.db.migrations import MIGRATIONS_DIR


@pytest.fixture
def db(tmp_path: Path) -> str:
    p = str(tmp_path / "s.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    return p


def test_table_is_created_by_migration_022(db):
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='dividends_throttle_pending'"
        ).fetchone()
        assert row is not None
        cols = [r[1] for r in con.execute(
            "PRAGMA table_info(dividends_throttle_pending)"
        ).fetchall()]
        # Schema must match migration 022 verbatim
        assert "figi" in cols
        assert "first_failed_at" in cols
        assert "last_failed_at" in cols
        assert "retry_count" in cols
    finally:
        con.close()


def test_pk_is_figi_so_upserts_dedup(db):
    con = sqlite3.connect(db)
    try:
        con.executescript("""
            INSERT INTO dividends_throttle_pending
              (figi, first_failed_at, last_failed_at, retry_count)
              VALUES ('F1', '2026-09-24T10:00:00', '2026-09-24T10:00:00', 1);
            -- second insert with same figi should fail (PK), not duplicate
        """)
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("""
                INSERT INTO dividends_throttle_pending
                  (figi, first_failed_at, last_failed_at, retry_count)
                  VALUES ('F1', '2026-09-24T10:01:00', '2026-09-24T10:01:00', 2);
            """)
    finally:
        con.close()


def test_drain_orders_by_last_failed_at_ascending(db):
    con = sqlite3.connect(db)
    try:
        con.executescript("""
            INSERT INTO dividends_throttle_pending
              VALUES ('F_A','2026-09-24T10:00:00','2026-09-24T10:00:00',1);
            INSERT INTO dividends_throttle_pending
              VALUES ('F_B','2026-09-24T09:00:00','2026-09-24T09:00:00',1);
            INSERT INTO dividends_throttle_pending
              VALUES ('F_C','2026-09-24T11:00:00','2026-09-24T11:00:00',1);
        """)
        con.commit()
        rows = con.execute(
            "SELECT figi FROM dividends_throttle_pending "
            "ORDER BY last_failed_at ASC"
        ).fetchall()
        assert [r[0] for r in rows] == ["F_B", "F_A", "F_C"]
    finally:
        con.close()
