"""Tests for priority-aware backfill queue construction.

Validates OpenSpec change `priority-aware-backfill`: the chain must
process figis ordered by gap size (descending) so rate-limit budget
is spent on the biggest gaps first.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from algotrader_api.db.sqlite import get_connection
from algotrader_api.ingestion.backfill import compute_missing_dates


def _make_test_db(tmp_path: Path) -> str:
    """Create a minimal sqlite DB with the tables the priority helper needs."""
    db_path = str(tmp_path / "test.db")
    con = get_connection(db_path)
    con.execute(
        "CREATE TABLE bars ("
        "  figi TEXT NOT NULL, ts DATE NOT NULL, "
        "  open REAL, high REAL, low REAL, close REAL, "
        "  volume INTEGER, source TEXT DEFAULT 'moex', "
        "  PRIMARY KEY (figi, ts))"
    )
    con.execute(
        "CREATE TABLE moex_holidays ("
        "  date DATE PRIMARY KEY, name TEXT)"
    )
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
    return db_path


def _seed_bars(db_path: str, figi: str, dates: list[str]):
    con = get_connection(db_path)
    for d in dates:
        con.execute(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (figi, d, 100.0, 101.0, 99.0, 100.5, 1000),
        )
    con.commit()


# Helper we'll implement
from algotrader_api.ingestion.priority import compute_priority_queue  # noqa: E402


# Scenario 1: Empty universe returns empty queue
def test_empty_universe_returns_empty_queue(tmp_path):
    db_path = _make_test_db(tmp_path)
    queue = compute_priority_queue(
        db_path=db_path,
        universe=[],
        listed_from_lookup=lambda t: date(2014, 1, 1),
        yesterday=date(2026, 9, 17),
    )
    assert queue == []


# Scenario 2: Fully covered figis excluded
def test_fully_covered_figis_excluded(tmp_path):
    db_path = _make_test_db(tmp_path)
    # Seed all weekdays 2026-01-01..2026-01-29
    weekdays = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06",
                "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12",
                "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16",
                "2026-01-19", "2026-01-20", "2026-01-21", "2026-01-22",
                "2026-01-23", "2026-01-26", "2026-01-27", "2026-01-28",
                "2026-01-29"]
    _seed_bars(db_path, "BBG_FULL", weekdays)
    queue = compute_priority_queue(
        db_path=db_path,
        universe=[("FULL", "BBG_FULL")],
        listed_from_lookup=lambda t: date(2026, 1, 1),
        yesterday=date(2026, 1, 29),
    )
    assert queue == []


# Scenario 3: Mixed coverage sorted by gap descending
def test_mixed_coverage_sorted_by_gap_descending(tmp_path):
    db_path = _make_test_db(tmp_path)
    # F1: only 1 bar in short window = 20 missing dates
    _seed_bars(db_path, "BBG_F1", ["2026-01-05"])
    # F2: half coverage in short window = 11 missing dates
    _seed_bars(db_path, "BBG_F2", ["2026-01-05", "2026-01-06", "2026-01-07",
                                     "2026-01-08", "2026-01-09", "2026-01-12",
                                     "2026-01-13", "2026-01-14", "2026-01-15",
                                     "2026-01-16"])
    # F3: full coverage
    weekdays = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06",
                "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12",
                "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16",
                "2026-01-19", "2026-01-20", "2026-01-21", "2026-01-22",
                "2026-01-23", "2026-01-26", "2026-01-27", "2026-01-28",
                "2026-01-29"]
    _seed_bars(db_path, "BBG_F3", weekdays)
    # threshold=0 to include all gaps (F1=20, F2=11)
    queue = compute_priority_queue(
        db_path=db_path,
        universe=[("F3", "BBG_F3"), ("F1", "BBG_F1"), ("F2", "BBG_F2")],
        listed_from_lookup=lambda t: date(2026, 1, 1),
        yesterday=date(2026, 1, 29),
        gap_threshold_for_moex=0,
    )
    # F3 should be excluded (full coverage), F1 first (most gaps)
    figis_in_order = [q[1] for q in queue]
    assert "BBG_F3" not in figis_in_order
    assert figis_in_order.index("BBG_F1") < figis_in_order.index("BBG_F2")
    # Verify gap sizes are descending
    gap_sizes = [q[2] for q in queue]
    assert gap_sizes == sorted(gap_sizes, reverse=True)


# Scenario 4: Default threshold=100 excludes tiny gaps
def test_default_threshold_excludes_tiny_gaps(tmp_path):
    db_path = _make_test_db(tmp_path)
    # 5 missing dates
    weekdays = ["2026-01-05", "2026-01-06", "2026-01-07",
                "2026-01-08", "2026-01-09", "2026-01-12",
                "2026-01-13", "2026-01-14", "2026-01-15",
                "2026-01-16", "2026-01-19", "2026-01-20",
                "2026-01-21", "2026-01-22", "2026-01-23",
                "2026-01-26", "2026-01-27", "2026-01-28",
                "2026-01-29"]
    _seed_bars(db_path, "BBG_SMALL", weekdays)
    queue = compute_priority_queue(
        db_path=db_path,
        universe=[("SMALL", "BBG_SMALL")],
        listed_from_lookup=lambda t: date(2026, 1, 1),
        yesterday=date(2026, 1, 29),
    )
    # Should be excluded (gap < threshold default of 100)
    # Handled by Tinkoff-only path, not MOEX walk.
    assert queue == []


# Scenario 5: gap_threshold_for_moex filter
def test_gap_threshold_filters_below_threshold(tmp_path):
    db_path = _make_test_db(tmp_path)
    # F1: 5 bars in 21 weekdays = 16 missing dates (below threshold)
    _seed_bars(db_path, "BBG_F1", ["2026-01-05", "2026-01-06", "2026-01-07",
                                     "2026-01-08", "2026-01-09"])
    # F2: only 1 bar = ~20 missing dates (still below threshold)
    # To test threshold filtering, F1 must have gap > threshold
    # Use a much bigger window for F2 to push gap above threshold
    # Actually simpler: F2 has 150 missing dates via longer window
    _seed_bars(db_path, "BBG_F2", ["2026-01-05"])
    # For F2, use longer listed_from → bigger gap
    queue = compute_priority_queue(
        db_path=db_path,
        universe=[("F1", "BBG_F1")],  # F1: 16 missing dates, below threshold
        listed_from_lookup=lambda t: date(2026, 1, 1),
        yesterday=date(2026, 1, 29),
        gap_threshold_for_moex=100,
    )
    # F1 has 16 missing dates, below threshold=100 → excluded
    assert queue == []
    # Now with F2 using longer window
    def lookup(t):
        if t == "F1":
            return date(2026, 1, 1)  # 16 missing
        if t == "F2":
            return date(2024, 1, 1)  # ~700 missing (>100)
        return date(2026, 1, 1)
    queue = compute_priority_queue(
        db_path=db_path,
        universe=[("F1", "BBG_F1"), ("F2", "BBG_F2")],
        listed_from_lookup=lookup,
        yesterday=date(2026, 1, 29),
        gap_threshold_for_moex=100,
    )
    # Only F2 (gap > 100) is in queue
    assert len(queue) == 1
    assert queue[0][1] == "BBG_F2"


# Scenario 5b: threshold=0 includes all gaps
def test_threshold_zero_includes_all_gaps(tmp_path):
    db_path = _make_test_db(tmp_path)
    # F1: 1 bar (gaps)
    _seed_bars(db_path, "BBG_F1", ["2026-01-05"])
    # F2: half coverage
    _seed_bars(db_path, "BBG_F2", ["2026-01-05", "2026-01-06", "2026-01-07",
                                     "2026-01-08", "2026-01-09", "2026-01-12",
                                     "2026-01-13", "2026-01-14", "2026-01-15",
                                     "2026-01-16"])
    queue = compute_priority_queue(
        db_path=db_path,
        universe=[("F1", "BBG_F1"), ("F2", "BBG_F2")],
        listed_from_lookup=lambda t: date(2026, 1, 1),
        yesterday=date(2026, 1, 29),
        gap_threshold_for_moex=0,
    )
    assert len(queue) == 2
    # F1 (most gaps) first
    assert queue[0][1] == "BBG_F1"
    assert queue[1][1] == "BBG_F2"


# Scenario 6: Idempotency — same args produce same queue
def test_idempotent_same_args_same_queue(tmp_path):
    db_path = _make_test_db(tmp_path)
    _seed_bars(db_path, "BBG_F1", ["2026-01-05"])
    universe = [("F1", "BBG_F1"), ("F2", "BBG_F2")]
    args = dict(
        db_path=db_path,
        universe=universe,
        listed_from_lookup=lambda t: date(2026, 1, 1),
        yesterday=date(2026, 1, 29),
    )
    queue1 = compute_priority_queue(**args)
    queue2 = compute_priority_queue(**args)
    assert queue1 == queue2
