"""Tests for the data-quality completeness module."""
from __future__ import annotations

from datetime import date

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations
from algotrader_api.data_quality.completeness import find_gap_intervals


@pytest.fixture
def db(tmp_path):
    """A fresh state.db with the schema applied (including moex_holidays)."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    return p


def _seed_instrument_and_bars(db, figi, dates):
    from algotrader_api.db.sqlite import get_connection
    cur = get_connection(db).cursor()
    cur.execute(
        "INSERT INTO instruments(ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("T" + figi[-3:], figi, "share", figi, "RUB", 1),
    )
    cur.executemany(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        [(figi, d, 100, 101, 99, 100, 1000) for d in dates],
    )
    cur.connection.commit()


def _seed_holidays(db, dates):
    from algotrader_api.db.sqlite import get_connection
    cur = get_connection(db).cursor()
    cur.executemany(
        "INSERT OR REPLACE INTO moex_holidays(date, name) VALUES (?, ?)",
        [(d, "test") for d in dates],
    )
    cur.connection.commit()


def test_find_gap_intervals_returns_empty_for_complete_history(db):
    figi = "FIGI-FULL"
    _seed_instrument_and_bars(db, figi, [
        "2024-01-10", "2024-01-15", "2024-01-22", "2024-01-29",
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert gaps == []


def test_find_gap_intervals_returns_gap_for_14_day_hole(db):
    figi = "FIGI-GAP"
    _seed_instrument_and_bars(db, figi, [
        "2024-03-01",   # before the gap
        "2024-03-22",   # after the gap (21 calendar days = 14 trading days)
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert len(gaps) == 1
    start, end = gaps[0]
    assert start == date(2024, 3, 1)
    assert end == date(2024, 3, 22)


def test_find_gap_intervals_ignores_weekend_only_gap(db):
    figi = "FIGI-WEEKEND"
    # Friday → Monday: 3 calendar days, 1 trading day — below threshold
    _seed_instrument_and_bars(db, figi, [
        "2024-03-08",  # Friday
        "2024-03-11",  # Monday
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert gaps == []


def test_find_gap_intervals_handles_holiday_in_gap(db):
    figi = "FIGI-HOL"
    # 8 calendar days, 6 weekdays, but with a 3-day MOEX holiday inside: 3 trading days
    # — still below threshold.
    _seed_holidays(db, ["2024-03-12", "2024-03-13", "2024-03-14"])
    _seed_instrument_and_bars(db, figi, [
        "2024-03-08",  # Friday
        "2024-03-18",  # Monday (8 calendar days)
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert gaps == []  # holiday adjustment brought it below threshold


def test_find_gap_intervals_returns_empty_for_single_bar(db):
    figi = "FIGI-SINGLE"
    _seed_instrument_and_bars(db, figi, ["2024-03-08"])
    assert find_gap_intervals(db, figi, min_gap_days=5) == []


def test_find_gap_intervals_is_idempotent(db):
    figi = "FIGI-IDEMP"
    _seed_instrument_and_bars(db, figi, [
        "2024-03-01",
        "2024-03-22",
    ])
    first = find_gap_intervals(db, figi, min_gap_days=5)
    second = find_gap_intervals(db, figi, min_gap_days=5)
    assert first == second