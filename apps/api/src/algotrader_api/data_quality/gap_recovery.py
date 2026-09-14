"""Detect and fill missing trading days in the bars table.

The data-quality pipeline already computes per-figi health reports; this
module is the "fill the holes" step. It scans every figi with at least
one bar in ``bars`` and emits a ``BarGap`` for every contiguous block of
missing trading days between ``min(ts)`` and ``max(ts)``. Weekends and
``restricted_periods`` dates are excluded from the "expected" set so a
suspended-trading day does not show up as a gap.

``recover_gaps`` is a thin runner that forwards each gap to a duck-typed
``runner.run_one(figi, ticker, from_, to_)``. The actual implementation
of the runner (BackfillRunner) is wired up by Task 8; this module is
decoupled from it so the gap-detection logic can be tested in isolation.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class BarGap:
    figi: str
    from_: date
    to_: date


def _open(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _load_restricted_dates(
    con: sqlite3.Connection, start: date, end: date
) -> set[str]:
    """Return ISO date strings in [start, end] that appear in restricted_periods."""
    rows = con.execute(
        "SELECT date FROM restricted_periods WHERE date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return {r["date"] for r in rows}


def _weekdays_minus_restricted(
    start: date, end: date, restricted: set[str]
) -> set[date]:
    """All weekdays in [start, end] with restricted dates removed."""
    out: set[date] = set()
    for i in range((end - start).days + 1):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:  # Saturday or Sunday
            continue
        if d.isoformat() in restricted:
            continue
        out.add(d)
    return out


def _collapse_missing(missing: set[date]) -> list[tuple[date, date]]:
    """Collapse a set of missing dates into contiguous (from, to) ranges.

    Returns ranges sorted by ``from``. Single missing days become
    ``(d, d)`` ranges — callers fill exactly that one day.
    """
    if not missing:
        return []
    sorted_dates = sorted(missing)
    ranges: list[tuple[date, date]] = []
    run_start = sorted_dates[0]
    prev = sorted_dates[0]
    for d in sorted_dates[1:]:
        if d == prev + timedelta(days=1):
            prev = d
            continue
        ranges.append((run_start, prev))
        run_start = d
        prev = d
    ranges.append((run_start, prev))
    return ranges


def find_gaps(db_path: str) -> list[BarGap]:
    """Return one BarGap per contiguous missing-trading-day range per figi.

    Algorithm per figi:
    1. Get all ``ts`` values for the figi from ``bars``.
    2. Compute expected days = set of weekdays in [min(ts), max(ts)],
       minus ``restricted_periods``, minus weekends.
    3. actual = set(ts) for the figi.
    4. missing = expected - actual.
    5. Collapse consecutive missing days into one ``BarGap``.

    Figis with zero bars are skipped (full-history backfill handles them).
    """
    con = _open(db_path)
    try:
        rows = con.execute(
            "SELECT figi, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
            "FROM bars GROUP BY figi"
        ).fetchall()
        figi_ranges = [
            (r["figi"], date.fromisoformat(r["first_ts"]), date.fromisoformat(r["last_ts"]))
            for r in rows
        ]
    finally:
        con.close()

    out: list[BarGap] = []
    for figi, first_ts, last_ts in figi_ranges:
        con = _open(db_path)
        try:
            restricted = _load_restricted_dates(con, first_ts, last_ts)
            expected = _weekdays_minus_restricted(first_ts, last_ts, restricted)
            actual_rows = con.execute(
                "SELECT ts FROM bars WHERE figi = ?", (figi,)
            ).fetchall()
        finally:
            con.close()
        actual = {date.fromisoformat(r["ts"]) for r in actual_rows}
        missing = expected - actual
        for from_d, to_d in _collapse_missing(missing):
            out.append(BarGap(figi=figi, from_=from_d, to_=to_d))
    return out


def recover_gaps(
    db_path: str,
    runner: object,  # duck-typed: needs .run_one(figi, ticker, from_, to_)
    gaps: list[BarGap],
) -> dict[str, int]:
    """For each BarGap, call ``runner.run_one(figi, ticker, from_, to_)``.

    Returns ``{figi: bars_added}`` for diagnostics. The ticker for each
    figi is looked up from the ``instruments`` table; figis without an
    instrument row are skipped (the full-history backfill in Task 7 owns
    their ticker resolution).
    """
    if not gaps:
        return {}
    figis = list({g.figi for g in gaps})
    con = _open(db_path)
    try:
        placeholders = ",".join("?" for _ in figis)
        ticker_rows = con.execute(
            f"SELECT figi, ticker FROM instruments WHERE figi IN ({placeholders})",
            tuple(figis),
        ).fetchall()
        ticker_by_figi = {r["figi"]: r["ticker"] for r in ticker_rows}
    finally:
        con.close()

    added: dict[str, int] = {}
    for gap in gaps:
        ticker = ticker_by_figi.get(gap.figi)
        if ticker is None:
            continue
        result = runner.run_one(gap.figi, ticker, gap.from_, gap.to_)
        added[gap.figi] = added.get(gap.figi, 0) + int(result or 0)
    return added