"""Tests for the `dividends` table (migration 014)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from algotrader_api.db.sqlite import run_migrations
from algotrader_api.db.migrations import MIGRATIONS_DIR


EXPECTED_COLUMNS = {
    "figi", "ex_date", "pay_date", "record_date", "declared_at",
    "period_year", "period_no", "currency", "amount_per_share",
    "fx_rate_used", "dividend_type", "regularity", "close_price",
    "yield_value", "yield_pct", "tax_withheld_pct", "cancelled_at",
    "source", "source_revision_ts", "retrieved_at", "revision_n",
    "is_retroactive", "amount_per_share_rub", "note",
}


@pytest.fixture
def dividends_db(tmp_path: Path) -> str:
    """A fresh DB with all migrations applied (including 014)."""
    db = str(tmp_path / "test.db")
    run_migrations(db, MIGRATIONS_DIR)
    return db


def _open(db: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def test_migration_014_creates_dividends_table(dividends_db: str):
    """The 014 migration creates `dividends` with all 24 expected columns."""
    conn = _open(dividends_db)
    try:
        # table_xinfo lists all columns including STORED generated ones
        # (plain PRAGMA table_info omits generated columns).
        rows = conn.execute("PRAGMA table_xinfo(dividends)").fetchall()
    finally:
        conn.close()
    names = {r["name"] for r in rows}
    assert names == EXPECTED_COLUMNS, (
        f"missing={EXPECTED_COLUMNS - names}, extra={names - EXPECTED_COLUMNS}"
    )


def test_migration_014_is_idempotent(tmp_path: Path):
    """Running the migration suite twice on the same DB must not raise."""
    db = str(tmp_path / "test.db")
    run_migrations(db, MIGRATIONS_DIR)
    run_migrations(db, MIGRATIONS_DIR)  # second run: no-op
    conn = _open(db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM dividends").fetchone()[0]
    finally:
        conn.close()
    assert n == 0  # empty, but the table exists


def test_dividends_pk_blocks_duplicate_revision(dividends_db: str):
    """PK (figi, ex_date, period_year, period_no, revision_n) blocks duplicates.

    Inserting revision_n=2 of the same event is allowed (distinct tuple).
    Re-inserting revision_n=1 of the same event collides on the PK and is a no-op
    via INSERT OR IGNORE.
    """
    base = (
        "INSERT OR IGNORE INTO dividends "
        "(figi, ex_date, period_year, period_no, revision_n, "
        " amount_per_share, retrieved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    args_rev1 = ("BBG000N9WN27", "2024-07-15", 2023, 1, 1, 25.0, "2024-07-16T10:00:00Z")
    args_rev2 = ("BBG000N9WN27", "2024-07-15", 2023, 1, 2, 27.0, "2025-04-10T10:00:00Z")
    args_rev1_dup = ("BBG000N9WN27", "2024-07-15", 2023, 1, 1, 999.0, "2025-04-10T10:00:00Z")

    conn = _open(dividends_db)
    try:
        cur = conn.execute(base, args_rev1)
        conn.commit()
        assert cur.rowcount == 1

        cur = conn.execute(base, args_rev2)
        conn.commit()
        assert cur.rowcount == 1  # distinct PK tuple: allowed

        cur = conn.execute(base, args_rev1_dup)
        conn.commit()
        assert cur.rowcount == 0  # duplicate PK tuple: IGNORE makes it a no-op

        rows = conn.execute(
            "SELECT revision_n, amount_per_share FROM dividends "
            "WHERE figi=? AND ex_date=? ORDER BY revision_n",
            ("BBG000N9WN27", "2024-07-15"),
        ).fetchall()
    finally:
        conn.close()

    assert [r["revision_n"] for r in rows] == [1, 2]
    # The duplicate INSERT must NOT have overwritten rev 1.
    assert rows[0]["amount_per_share"] == 25.0


def test_generated_columns(dividends_db: str):
    """amount_per_share_rub = amount_per_share when fx_rate_used IS NULL,
    otherwise amount_per_share * fx_rate_used."""
    base = (
        "INSERT INTO dividends "
        "(figi, ex_date, period_year, period_no, revision_n, "
        " amount_per_share, fx_rate_used, retrieved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    conn = _open(dividends_db)
    try:
        # RUB: fx_rate_used IS NULL -> amount_per_share_rub == amount_per_share
        conn.execute(
            base,
            ("F_RUB", "2024-05-20", 2023, 1, 1, 10.0, None, "2024-05-21T10:00:00Z"),
        )
        # Foreign currency: fx_rate_used = 2.0 -> rub = amount * 2.0
        conn.execute(
            base,
            ("F_USD", "2024-05-20", 2023, 1, 1, 5.0, 2.0, "2024-05-21T10:00:00Z"),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT figi, amount_per_share, fx_rate_used, amount_per_share_rub "
            "FROM dividends ORDER BY figi"
        ).fetchall()
    finally:
        conn.close()

    rub_row = next(r for r in rows if r["figi"] == "F_RUB")
    usd_row = next(r for r in rows if r["figi"] == "F_USD")
    assert rub_row["fx_rate_used"] is None
    assert rub_row["amount_per_share_rub"] == pytest.approx(10.0)
    assert usd_row["fx_rate_used"] == pytest.approx(2.0)
    assert usd_row["amount_per_share_rub"] == pytest.approx(10.0)


def test_is_retroactive_is_generated(dividends_db: str):
    """is_retroactive = 0 for revision_n=1, = 1 for revision_n>1."""
    base = (
        "INSERT INTO dividends "
        "(figi, ex_date, period_year, period_no, revision_n, "
        " amount_per_share, retrieved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    conn = _open(dividends_db)
    try:
        conn.execute(base, ("F_REV", "2024-06-10", 2023, 1, 1, 8.0, "2024-06-11T10:00:00Z"))
        conn.execute(base, ("F_REV", "2024-06-10", 2023, 1, 2, 9.0, "2025-03-01T10:00:00Z"))
        conn.commit()
        rows = conn.execute(
            "SELECT revision_n, is_retroactive FROM dividends "
            "WHERE figi='F_REV' ORDER BY revision_n"
        ).fetchall()
    finally:
        conn.close()

    assert rows[0]["revision_n"] == 1
    assert rows[0]["is_retroactive"] == 0
    assert rows[1]["revision_n"] == 2
    assert rows[1]["is_retroactive"] == 1
