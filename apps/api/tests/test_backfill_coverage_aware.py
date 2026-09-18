"""Coverage-aware bars backfill tests.

Validates the OpenSpec change `fix-bars-coverage-aware-backfill`:
for each figi, the chain must compute the set of expected trading dates
in [listed_from, yesterday] missing from `bars`, and walk only those
windows.

Helper tested: `compute_missing_dates` in
`algotrader_api.ingestion.backfill`.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from algotrader_api.db.sqlite import get_connection
from algotrader_api.ingestion.backfill import compute_missing_dates


def _make_test_db(tmp_path: Path) -> str:
    """Create a minimal sqlite DB with the tables the helper needs.

    Mirrors the relevant subset of production migrations (004 for
    moex_holidays, 012 for bars/instrument_metadata).
    """
    db_path = str(tmp_path / "test.db")
    con = get_connection(db_path)
    # bars table (subset of cols used by the helper)
    con.execute(
        "CREATE TABLE bars ("
        "  figi TEXT NOT NULL, "
        "  ts DATE NOT NULL, "
        "  open REAL, high REAL, low REAL, close REAL, "
        "  volume INTEGER, source TEXT DEFAULT 'moex', "
        "  PRIMARY KEY (figi, ts))"
    )
    # moex_holidays table — column is `date` per migration 004
    con.execute(
        "CREATE TABLE moex_holidays ("
        "  date DATE PRIMARY KEY, name TEXT)"
    )
    # instrument_metadata table (not strictly needed for the helper
    # but present so replace_bars_for_figi() works on the same db
    # if any integration test uses the fixture).
    con.execute(
        "CREATE TABLE instrument_metadata ("
        "  figi TEXT PRIMARY KEY, "
        "  total_bars INTEGER, "
        "  first_bar_ts DATE, "
        "  last_bar_ts DATE, "
        "  last_run_status TEXT, "
        "  last_run_at TIMESTAMP)"
    )
    con.commit()
    # NOTE: do NOT close the singleton connection — see sqlite.py.
    return db_path


def _seed_bars(db_path: str, figi: str, dates: list[str]):
    """Insert one bar per date for figi."""
    con = get_connection(db_path)
    for d in dates:
        con.execute(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (figi, d, 100.0, 101.0, 99.0, 100.5, 1000),
        )
    con.commit()


# Scenario 1: empty figi (no bars) → return all expected dates in window
def test_figi_with_no_bars_returns_all_expected_dates(tmp_path):
    db_path = _make_test_db(tmp_path)
    # Weekdays in 2021-01: 1,4,5,6,7,8,11,12,13,14,15,18,19,20,21,22,25,26,27,28,29 = 21 days
    # No holidays → expect 21 missing
    missing = compute_missing_dates(
        figi="BBG0000776S2",
        listed_from=date(2021, 1, 1),
        yesterday=date(2021, 1, 29),
        db_path=db_path,
    )
    assert len(missing) == 21
    assert date(2021, 1, 1) in missing
    assert date(2021, 1, 29) in missing
    assert date(2021, 1, 2) not in missing  # Saturday
    assert date(2021, 1, 3) not in missing  # Sunday


# Scenario 2: bars exist for first part of window, missing rest
def test_figi_with_partial_history_returns_only_trailing_missing(tmp_path):
    db_path = _make_test_db(tmp_path)
    figi = "BBG0000776S2"
    # Seed bars for first 10 weekdays of 2021-01
    seeded = ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07", "2021-01-08",
              "2021-01-11", "2021-01-12", "2021-01-13", "2021-01-14", "2021-01-15"]
    _seed_bars(db_path, figi, seeded)
    missing = compute_missing_dates(
        figi=figi,
        listed_from=date(2021, 1, 1),
        yesterday=date(2021, 1, 29),
        db_path=db_path,
    )
    # 21 weekdays - 10 seeded = 11 missing (weekends excluded from expected)
    assert len(missing) == 11
    assert date(2021, 1, 1) in missing  # before seed
    assert date(2021, 1, 4) not in missing  # seeded
    assert date(2021, 1, 18) in missing  # after seed


# Scenario 3: bars fully cover window → return empty (skip signal)
def test_figi_fully_covered_returns_empty(tmp_path):
    db_path = _make_test_db(tmp_path)
    figi = "BBG0000776S2"
    # All 21 weekdays in 2021-01
    seeded = ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07", "2021-01-08",
              "2021-01-11", "2021-01-12", "2021-01-13", "2021-01-14", "2021-01-15",
              "2021-01-18", "2021-01-19", "2021-01-20", "2021-01-21", "2021-01-22",
              "2021-01-25", "2021-01-26", "2021-01-27", "2021-01-28", "2021-01-29"]
    _seed_bars(db_path, figi, seeded)
    # Also need 2021-01-01 (Fri) → actually 21 weekdays, count is 21 in Jan 2021
    # 1, 4-8, 11-15, 18-22, 25-29 = 21 days. Add 01.
    _seed_bars(db_path, figi, ["2021-01-01"])
    missing = compute_missing_dates(
        figi=figi,
        listed_from=date(2021, 1, 1),
        yesterday=date(2021, 1, 29),
        db_path=db_path,
    )
    assert missing == set()


# Scenario 4: mid-window gap → return only that window
def test_figi_with_mid_window_gap_returns_only_gap(tmp_path):
    db_path = _make_test_db(tmp_path)
    figi = "BBG0000776S2"
    # Seed 2013-03-25 through 2013-03-28 (Mon-Thu) and 2013-04-01 through 2013-04-12
    # Gap = 2013-03-29 (Fri), 2013-04-01 — wait, 04-01 is in second batch
    # Gap = 2013-03-29 (Fri)
    seeded_pre = ["2013-03-25", "2013-03-26", "2013-03-27", "2013-03-28"]
    seeded_post = ["2013-04-01", "2013-04-02", "2013-04-03", "2013-04-04", "2013-04-05"]
    _seed_bars(db_path, figi, seeded_pre + seeded_post)
    missing = compute_missing_dates(
        figi=figi,
        listed_from=date(2013, 3, 20),
        yesterday=date(2013, 4, 10),
        db_path=db_path,
    )
    # Missing: 2013-03-29 (Fri) only
    assert date(2013, 3, 29) in missing
    # Also: 03-20 (Wed), 03-21 (Thu), 03-22 (Fri) since not seeded
    assert date(2013, 3, 20) in missing
    # Already seeded should not be missing
    assert date(2013, 3, 25) not in missing
    assert date(2013, 4, 1) not in missing


# Scenario 5: holidays are excluded from expected set
def test_holidays_excluded_from_expected_set(tmp_path):
    db_path = _make_test_db(tmp_path)
    figi = "BBG0000776S2"
    # Mark 2021-05-03 (Mon) as holiday
    con = get_connection(db_path)
    con.execute("INSERT INTO moex_holidays (date, name) VALUES (?, ?)",
                ("2021-05-03", "Test Holiday"))
    con.commit()
    missing = compute_missing_dates(
        figi=figi,
        listed_from=date(2021, 5, 1),
        yesterday=date(2021, 5, 7),
        db_path=db_path,
    )
    # Weekdays: 5/3 Mon, 5/4 Tue, 5/5 Wed, 5/6 Thu, 5/7 Fri → 5 weekdays
    # Minus holiday 5/3 → 4 expected
    assert len(missing) == 4
    assert date(2021, 5, 3) not in missing  # excluded as holiday


# Scenario 6: window edge cases
def test_empty_window_returns_empty(tmp_path):
    db_path = _make_test_db(tmp_path)
    missing = compute_missing_dates(
        figi="BBG0000776S2",
        listed_from=date(2021, 1, 1),
        yesterday=date(2020, 12, 31),  # inverted
        db_path=db_path,
    )
    assert missing == set()


# Scenario 7: bars sourced from BOTH moex and tinkoff count as existing
def test_existing_bars_from_any_source_count_as_covered(tmp_path):
    db_path = _make_test_db(tmp_path)
    figi = "BBG0000776S2"
    con = get_connection(db_path)
    # Insert one bar with source='tinkoff'
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (figi, "2021-01-04", 100, 101, 99, 100.5, 1000, "tinkoff"),
    )
    con.commit()
    missing = compute_missing_dates(
        figi=figi,
        listed_from=date(2021, 1, 1),
        yesterday=date(2021, 1, 8),
        db_path=db_path,
    )
    # Weekdays in [01-01, 01-08] = 01-01 (Fri), 01-04 (Mon),
    # 01-05, 01-06, 01-07, 01-08 = 6 days. Minus 01-04 (seeded)
    # = 5 missing.
    assert len(missing) == 5
    assert date(2021, 1, 4) not in missing


# Scenario 8: pure function (no writes), safe to call twice
def test_pure_function_idempotent(tmp_path):
    db_path = _make_test_db(tmp_path)
    figi = "BBG0000776S2"
    _seed_bars(db_path, figi, ["2021-01-04", "2021-01-05"])
    missing1 = compute_missing_dates(
        figi=figi, listed_from=date(2021, 1, 1),
        yesterday=date(2021, 1, 8), db_path=db_path,
    )
    missing2 = compute_missing_dates(
        figi=figi, listed_from=date(2021, 1, 1),
        yesterday=date(2021, 1, 8), db_path=db_path,
    )
    assert missing1 == missing2
    # DB unchanged: bars count still 2
    con = get_connection(db_path)
    cnt = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    assert cnt == 2
