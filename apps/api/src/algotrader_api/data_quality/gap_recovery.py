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


EPOCH = date(1970, 1, 1)


@dataclass(frozen=True)
class BarGap:
    figi: str
    from_: date
    to_: date


def _open(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _to_day_int(iso: str) -> int:
    """Convert ISO date string to days since 1970-01-01."""
    return (date.fromisoformat(iso) - EPOCH).days


def _expected_weekdays_int(fst_int: int, lst_int: int) -> set[int]:
    """All weekday day-ints in [fst_int, lst_int].

    Uses (day + 3) % 7 < 5 for Mon..Fri: 1970-01-01 was a Thursday,
    so Thursday maps to (0 + 3) % 7 = 3; adding 3 re-aligns the
    Mon=0 .. Sun=6 weekday index to a ``day_int`` offset.
    """
    return {
        d for d in range(fst_int, lst_int + 1)
        if (d + 3) % 7 < 5
    }


def _collapse_missing_int(missing: set[int]) -> list[tuple[int, int]]:
    """Collapse a set of missing day-ints into contiguous (from, to) ranges.

    Returns ranges sorted by ``from``. Single missing days become
    ``(d, d)`` ranges — callers fill exactly that one day.
    """
    if not missing:
        return []
    sorted_days = sorted(missing)
    ranges: list[tuple[int, int]] = []
    run_start = sorted_days[0]
    prev = sorted_days[0]
    for d in sorted_days[1:]:
        if d == prev + 1:
            prev = d
            continue
        ranges.append((run_start, prev))
        run_start = d
        prev = d
    ranges.append((run_start, prev))
    return ranges


def find_gaps(db_path: str) -> list[BarGap]:
    """Return one BarGap per contiguous missing-trading-day range per figi.

    Optimised rewrite:
    1. Bulk-load static data once: holidays + restricted_periods as int sets.
    2. Per-figi loop uses indexed ``SELECT ts FROM bars WHERE figi = ?``
       (still optimal — full-table scan is 2 s slower on this DB size).
       A single reused connection avoids 3796 open/close cycles.
    3. Expected days are computed in integer arithmetic, not per-day
       ``date`` objects, then converted back to ``date`` only at BarGap
       emission.
    """
    con = _open(db_path)
    try:
        # One connection for the whole call. Hoisted queries:
        rows = con.execute(
            "SELECT figi, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
            "FROM bars GROUP BY figi"
        ).fetchall()
        holidays_int = {
            _to_day_int(r["date"])
            for r in con.execute("SELECT date FROM moex_holidays").fetchall()
        }
        restricted_int = {
            _to_day_int(r["date"])
            for r in con.execute("SELECT date FROM restricted_periods").fetchall()
        }

        out: list[BarGap] = []
        for r in rows:
            figi = r["figi"]
            fst_int = _to_day_int(r["first_ts"])
            lst_int = _to_day_int(r["last_ts"])
            expected = _expected_weekdays_int(fst_int, lst_int)
            expected -= restricted_int
            expected -= holidays_int

            # Per-figi indexed ts query.
            actual_rows = con.execute(
                "SELECT ts FROM bars WHERE figi = ?", (figi,)
            ).fetchall()
            actual = {_to_day_int(row["ts"]) for row in actual_rows}

            missing = expected - actual
            for from_int, to_int in _collapse_missing_int(missing):
                out.append(BarGap(
                    figi=figi,
                    from_=EPOCH + timedelta(days=from_int),
                    to_=EPOCH + timedelta(days=to_int),
                ))
        return out
    finally:
        con.close()


async def recover_gaps(
    db_path: str,
    runner: object,  # duck-typed: needs ._backfill_one(*, figi, ticker, from_, to_) (async)
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
        result = await runner._backfill_one(
            figi=gap.figi, ticker=ticker, from_=gap.from_, to=gap.to_,
        )
        added[gap.figi] = added.get(gap.figi, 0) + int(result or 0)
    return added