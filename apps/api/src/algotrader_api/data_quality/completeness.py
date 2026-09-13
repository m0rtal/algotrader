"""Detect and backfill historical gaps in figi bars data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from ..db.sqlite import get_connection


def find_gap_intervals(
    db: str, figi: str, min_gap_days: int = 5,
) -> list[tuple[date, date]]:
    """Find intervals where bars are missing beyond `min_gap_days`
    trading days (excluding MOEX holidays in range).

    Returns a list of (start, end) tuples where:
    - `start` is the date of the last bar BEFORE the gap.
    - `end` is the date of the next bar AFTER the gap.

    The caller is responsible for fetching [start + 1, end] from the broker.
    """
    con = get_connection(db)
    rows = con.execute(
        "SELECT ts FROM bars WHERE figi = ? ORDER BY ts",
        (figi,),
    ).fetchall()
    if len(rows) < 2:
        return []
    bars = [date.fromisoformat(r[0]) for r in rows]
    gaps: list[tuple[date, date]] = []
    for prev, cur in zip(bars, bars[1:]):
        cal_days = (cur - prev).days
        # Subtract weekends and MOEX holidays from the calendar gap.
        weekdays = sum(
            1 for i in range(cal_days)
            if (prev + timedelta(days=i + 1)).weekday() < 5
        )
        holiday_rows = con.execute(
            "SELECT COUNT(*) FROM moex_holidays WHERE date > ? AND date < ?",
            (prev.isoformat(), cur.isoformat()),
        ).fetchone()
        trading_gap = weekdays - (holiday_rows[0] if holiday_rows else 0)
        if trading_gap > min_gap_days:
            gaps.append((prev, cur))
    return gaps