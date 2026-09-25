"""Coverage helpers for the consumption-time gate (PR-3).

Used by:
- apps/api/scripts/populate_expected_bars.py (one-shot backfill)
- apps/api/src/algotrader_api/ml/features.py (gate check)

Backed by the existing moex_holidays table (migration 006, seeded
2020-2027).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Iterable


def expected_business_days(
    conn: sqlite3.Connection,
    listing_date: date,
    end_date: date,
) -> int:
    """Count business days from listing_date to end_date (inclusive),
    excluding MOEX holidays from the moex_holidays table.

    O(days) per call. Use the cached instruments.expected_bars column
    on the hot path; this helper is for one-shot backfill / refresh.

    Args:
        conn: open SQLite connection (read-only is fine).
        listing_date: first trading day for the figi (instruments.source_updated_at).
        end_date: last trading day to count up to (typically yesterday).

    Returns:
        0 if listing_date > end_date; otherwise the count of business days.
    """
    if listing_date > end_date:
        return 0
    rows = conn.execute(
        "SELECT date FROM moex_holidays WHERE date BETWEEN ? AND ?",
        (listing_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    holiday_set = {date.fromisoformat(r[0]) for r in rows}
    d = listing_date
    count = 0
    one = timedelta(days=1)
    while d <= end_date:
        if d.weekday() < 5 and d not in holiday_set:
            count += 1
        d += one
    return count
