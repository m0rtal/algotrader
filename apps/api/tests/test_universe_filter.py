"""Tests for the universe discovery layer.

Covers `discover_universe` (which classes are requested) and
`upsert_instruments` (which rows actually land in SQLite). Both
must respect `TRADEABLE_CLASSES` — the universe must not slowly
drift toward tens of thousands of non-tradeable figis.
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def empty_db(tmp_path):
    """A SQLite state.db with migrations applied but no rows."""
    from algotrader_api.db.migrations import MIGRATIONS_DIR
    from algotrader_api.db.sqlite import run_migrations

    db = str(tmp_path / "state.db")
    run_migrations(db, str(MIGRATIONS_DIR))
    return db


def test_upsert_instruments_drops_non_tradeable(empty_db):
    """Even if a caller hands `upsert_instruments` a mixed bag, only
    tradeable classes land in SQLite."""
    from algotrader_api.ingestion.universe import upsert_instruments

    rows = [
        {"ticker": "SBER", "figi": "FG-S", "class": "share",
         "name": "Sber", "currency": "rub", "lot_size": 10, "isin": None, "sector": None},
        {"ticker": "OFZ", "figi": "FG-B", "class": "bond",
         "name": "Bond", "currency": "rub", "lot_size": 1, "isin": None, "sector": None},
        {"ticker": "FXUS", "figi": "FG-E", "class": "etf",
         "name": "FXUS", "currency": "rub", "lot_size": 1, "isin": None, "sector": None},
        {"ticker": "OPT1", "figi": "FG-O", "class": "option",
         "name": "Opt", "currency": "rub", "lot_size": 1, "isin": None, "sector": None},
        {"ticker": "FUT1", "figi": "FG-F", "class": "future",
         "name": "Fut", "currency": "rub", "lot_size": 1, "isin": None, "sector": None},
    ]
    inserted = upsert_instruments(empty_db, rows)
    assert inserted == 3

    con = sqlite3.connect(empty_db)
    classes = sorted(r[0] for r in con.execute("SELECT class FROM instruments").fetchall())
    con.close()
    assert classes == ["bond", "etf", "share"]


def test_upsert_instruments_handles_all_empty(empty_db):
    """All non-tradeable rows → zero inserts, no error."""
    from algotrader_api.ingestion.universe import upsert_instruments

    rows = [
        {"ticker": "OPT", "figi": "FG-O", "class": "option",
         "name": "Opt", "currency": "rub", "lot_size": 1, "isin": None, "sector": None},
        {"ticker": "FUT", "figi": "FG-F", "class": "future",
         "name": "Fut", "currency": "rub", "lot_size": 1, "isin": None, "sector": None},
    ]
    inserted = upsert_instruments(empty_db, rows)
    assert inserted == 0

    con = sqlite3.connect(empty_db)
    n = con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
    con.close()
    assert n == 0
