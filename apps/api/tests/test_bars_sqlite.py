"""Tests for the `bars` SQLite table: write-path helpers and read path."""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from algotrader_api.db.bars_sqlite import replace_bars_for_figi, resolve_figi_for_ticker


@pytest.fixture
def bars_db(fresh_db):
    """`fresh_db` already ran every migration including 005_bars_table."""
    return fresh_db


# ─── write path ────────────────────────────────────────────────────────


def test_replace_bars_for_figi_inserts_new_rows(bars_db):
    """A fresh figi with N candles results in N rows in the bars table."""
    candles = [
        {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000},
        {"ts": "2026-09-02", "open": 105.0, "high": 112.0, "low": 100.0, "close": 110.0, "volume": 1100},
    ]
    written = replace_bars_for_figi(bars_db, "FIGI-1", candles)
    assert written == 2

    con = sqlite3.connect(bars_db)
    rows = con.execute(
        "SELECT ts, open, high, low, close, volume FROM bars WHERE figi = ? ORDER BY ts",
        ("FIGI-1",),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0] == ("2026-09-01", 100.0, 110.0, 95.0, 105.0, 1000)
    assert rows[1] == ("2026-09-02", 105.0, 112.0, 100.0, 110.0, 1100)
    con.close()


def test_replace_bars_for_figi_replaces_existing_rows(bars_db):
    """Calling replace twice for the same figi keeps the latest candles only."""
    old = [{"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000}]
    new = [{"ts": "2026-09-05", "open": 200.0, "high": 220.0, "low": 195.0, "close": 215.0, "volume": 2000}]

    replace_bars_for_figi(bars_db, "FIGI-1", old)
    replace_bars_for_figi(bars_db, "FIGI-1", new)

    con = sqlite3.connect(bars_db)
    rows = con.execute(
        "SELECT ts FROM bars WHERE figi = ? ORDER BY ts",
        ("FIGI-1",),
    ).fetchall()
    assert rows == [("2026-09-05",)]
    con.close()


def test_replace_bars_for_figi_accepts_date_objects(bars_db):
    """`ts` may come in as a `datetime.date` instead of an ISO string."""
    candles = [
        {"ts": date(2026, 9, 1), "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000},
    ]
    replace_bars_for_figi(bars_db, "FIGI-1", candles)

    con = sqlite3.connect(bars_db)
    row = con.execute("SELECT ts FROM bars WHERE figi = ?", ("FIGI-1",)).fetchone()
    assert row == ("2026-09-01",)
    con.close()


def test_replace_bars_for_figi_empty_list_noop(bars_db):
    """An empty candle list leaves the bars table untouched for that figi."""
    con = sqlite3.connect(bars_db)
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES ('FIGI-1', '2026-09-01', 100, 110, 95, 105, 1000)"
    )
    con.commit()
    con.close()

    written = replace_bars_for_figi(bars_db, "FIGI-1", [])
    assert written == 0

    con = sqlite3.connect(bars_db)
    rows = con.execute("SELECT COUNT(*) FROM bars WHERE figi = ?", ("FIGI-1",)).fetchone()
    assert rows[0] == 1
    con.close()


# ─── read path ────────────────────────────────────────────────────────


def test_resolve_figi_for_ticker_finds_by_ticker(bars_db):
    """`symbol` parameter is a ticker, not a figi."""
    con = sqlite3.connect(bars_db)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("SBER", "FIGI-SBER", "share", "Sber", "rub", 10),
    )
    con.commit()
    con.close()

    assert resolve_figi_for_ticker(bars_db, "SBER") == "FIGI-SBER"


def test_resolve_figi_for_ticker_falls_back_to_figi_match(bars_db):
    """`symbol` may be passed as the figi directly (no figi-resolution step)."""
    con = sqlite3.connect(bars_db)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("SBER", "FIGI-SBER", "share", "Sber", "rub", 10),
    )
    con.commit()
    con.close()

    assert resolve_figi_for_ticker(bars_db, "FIGI-SBER") == "FIGI-SBER"


def test_replace_bars_for_figi_rolls_back_on_error(bars_db):
    """If the transaction raises (e.g. instrument_metadata missing), the
    bars rows from this call must not leak into the table."""
    import sqlite3 as _sqlite

    # Drop the instrument_metadata row that replace_bars_for_figi wants
    # to UPDATE. This raises `no such column: ...` because the
    # subquery references an empty result — no, actually the columns
    # are present in the schema but the row's UPDATE matches nothing.
    # We need a real schema failure: drop the instrument_metadata
    # table to provoke an UPDATE failure mid-transaction.
    con = _sqlite.connect(bars_db)
    con.execute("DROP TABLE instrument_metadata")
    con.commit()
    con.close()

    candles = [
        {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000},
    ]
    with pytest.raises(_sqlite.OperationalError):
        replace_bars_for_figi(bars_db, "FIGI-1", candles)

    # After rollback, the bars row should not be persisted.
    con = _sqlite.connect(bars_db)
    cnt = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    con.close()
    assert cnt == 0, "rollback failed: bars row leaked after UPDATE error"


def test_list_bars_returns_ordered_dicts(bars_db):
    """list_bars returns one dict per row in ts-ascending order."""
    from algotrader_api.db.bars_sqlite import list_bars

    replace_bars_for_figi(
        bars_db,
        "FIGI-1",
        [
            {"ts": "2026-09-02", "open": 105.0, "high": 112.0, "low": 100.0, "close": 110.0, "volume": 1100},
            {"ts": "2026-09-01", "open": 100.0, "high": 110.0, "low": 95.0, "close": 105.0, "volume": 1000},
        ],
    )
    rows = list_bars(bars_db, "FIGI-1")
    assert [r["ts"] for r in rows] == ["2026-09-01", "2026-09-02"]


def test_count_bars_returns_total_rows(bars_db):
    """count_bars sums every figi's row count."""
    from algotrader_api.db.bars_sqlite import count_bars

    replace_bars_for_figi(
        bars_db,
        "FIGI-1",
        [{"ts": "2026-09-01", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1}],
    )
    replace_bars_for_figi(
        bars_db,
        "FIGI-2",
        [
            {"ts": "2026-09-01", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1},
            {"ts": "2026-09-02", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1},
        ],
    )
    assert count_bars(bars_db) == 3


def test_replace_bars_for_figi_accepts_candle_with_year_month_day_attrs(bars_db):
    """Candles without a `ts` field but with `time.year/month/day` attrs
    extract a date from those attrs (raw SDK output shape)."""
    from types import SimpleNamespace

    closed = SimpleNamespace(
        time=SimpleNamespace(year=2026, month=9, day=1),
        open=SimpleNamespace(units=100, nano=0),
        high=SimpleNamespace(units=110, nano=0),
        low=SimpleNamespace(units=95, nano=0),
        close=SimpleNamespace(units=105, nano=0),
        volume=1000,
    )
    replace_bars_for_figi(bars_db, "FIGI-NATIVE", [closed])

    con = sqlite3.connect(bars_db)
    row = con.execute(
        "SELECT ts FROM bars WHERE figi = ?", ("FIGI-NATIVE",)
    ).fetchone()
    con.close()
    assert row == ("2026-09-01",)


def test_replace_bars_for_figi_accepts_nested_time_dict(bars_db):
    """Raw gRPC responses serialise as dicts with a nested `time` dict."""
    closed = {
        "time": {"year": 2026, "month": 9, "day": 1},
        "open": {"units": 100, "nano": 0},
        "high": {"units": 110, "nano": 0},
        "low": {"units": 95, "nano": 0},
        "close": {"units": 105, "nano": 0},
        "volume": 1000,
    }
    replace_bars_for_figi(bars_db, "FIGI-NESTED", [closed])

    con = sqlite3.connect(bars_db)
    row = con.execute(
        "SELECT open, high, low, close FROM bars WHERE figi = ?",
        ("FIGI-NESTED",),
    ).fetchone()
    con.close()
    assert row == (100.0, 110.0, 95.0, 105.0)


def test_replace_bars_for_figi_raises_on_unparseable_ts(bars_db):
    """A candle with neither `ts` nor `time.year/month/day` raises so the
    runner can fall back to a warn-log and skip the figi."""
    with pytest.raises(ValueError):
        replace_bars_for_figi(bars_db, "FIGI-BAD", [{"open": 1, "high": 2, "low": 1, "close": 2, "volume": 1}])


def test_resolve_figi_for_ticker_unknown_returns_none(bars_db):
    assert resolve_figi_for_ticker(bars_db, "ZZZZ") is None
