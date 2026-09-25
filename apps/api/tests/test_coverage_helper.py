"""Tests for apps/api/src/algotrader_api/ml/coverage.py."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from algotrader_api.ml.coverage import expected_business_days


@pytest.fixture
def conn_with_holidays(tmp_path: Path):
    """In-memory SQLite with moex_holidays seeded (2026 calendar)."""
    con = sqlite3.connect(str(tmp_path / "test.db"))
    con.executescript("""
        CREATE TABLE moex_holidays (date TEXT PRIMARY KEY);
        -- 2026 Russia public holidays (sample; the real moex_holidays
        -- table has 2020-2027 seeded by migration 006)
        INSERT INTO moex_holidays VALUES ('2026-01-01');  -- New Year
        INSERT INTO moex_holidays VALUES ('2026-01-07');  -- Orthodox Christmas
        INSERT INTO moex_holidays VALUES ('2026-02-23');  -- Defender of Fatherland
        INSERT INTO moex_holidays VALUES ('2026-03-09');  -- Women's Day (Sun→Mon)
        INSERT INTO moex_holidays VALUES ('2026-05-01');  -- Spring & Labour
        INSERT INTO moex_holidays VALUES ('2026-05-11');  -- Victory Day (Sun→Mon)
        INSERT INTO moex_holidays VALUES ('2026-06-12');  -- Russia Day (Fri)
        INSERT INTO moex_holidays VALUES ('2026-11-04');  -- Unity Day (Wed)
    """)
    yield con
    con.close()


def test_expected_business_days_excludes_weekends(conn_with_holidays):
    """Mon-Fri = 5 business days, regardless of holidays in that span."""
    # 2026-03-02 (Mon) through 2026-03-06 (Fri) — no holidays
    assert expected_business_days(
        conn_with_holidays, date(2026, 3, 2), date(2026, 3, 6)
    ) == 5


def test_expected_business_days_excludes_moex_holidays(conn_with_holidays):
    """Holiday in the span must be excluded from the count."""
    # 2026-05-01 (Fri) is Spring & Labour — should be excluded
    # 2026-04-27 (Mon) through 2026-05-08 (Fri):
    #   Mon-Fri Apr 27-May 1: 4 business days (May 1 is holiday)
    #   Mon-Fri May 4-8: 5 business days
    #   Total: 9
    assert expected_business_days(
        conn_with_holidays, date(2026, 4, 27), date(2026, 5, 8)
    ) == 9


def test_expected_business_days_handles_listing_after_end(conn_with_holidays):
    """If listing_date > end_date, return 0."""
    assert expected_business_days(
        conn_with_holidays, date(2026, 12, 31), date(2026, 1, 1)
    ) == 0


def test_expected_business_days_inclusive_of_end_date(conn_with_holidays):
    """Both endpoints are inclusive."""
    # Single business day: 2026-03-02 (Mon, no holiday)
    assert expected_business_days(
        conn_with_holidays, date(2026, 3, 2), date(2026, 3, 2)
    ) == 1
    # Single holiday: 2026-01-01 (Thu, holiday)
    assert expected_business_days(
        conn_with_holidays, date(2026, 1, 1), date(2026, 1, 1)
    ) == 0
