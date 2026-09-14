"""Detect and backfill historical gaps in figi bars data."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from ..db.bars_sqlite import replace_bars_for_figi
from ..db.sqlite import get_connection
from .health import HealthIssue, HealthReport


_log = logging.getLogger(__name__)


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


@dataclass
class CompletenessSummary:
    """Aggregate result of one ``run_completeness_pass`` invocation.

    Returned to the caller (the daily guardian) so it can roll the
    counts into the pipeline row's detail string. Mirrors
    ``RecoverySummary`` in spirit: cheap counters, no nested state.
    """

    figis_examined: int = 0
    gaps_found: int = 0
    bars_added: int = 0
    exhausted: int = 0


def _candle_date(c: object) -> date | None:
    """Extract the date of a candle for the today-filter.

    The broker (real and fake) hands back ``dict``-shaped candles
    (``{"ts": "YYYY-MM-DD", ...}``). The raw gRPC dataclass shape is
    converted to dicts at the client boundary
    (``ingestion.real_client_convert._candle_to_dict``), so we never
    see dataclasses here.

    Returns ``None`` for anything that doesn't look like a dict with
    a parseable ``ts`` — that candle is then dropped by the
    ``< today`` filter alongside today's in-progress bar.
    """
    if not isinstance(c, dict):
        return None
    ts = c.get("ts")
    if isinstance(ts, str):
        try:
            return date.fromisoformat(ts[:10])
        except ValueError:
            return None
    return None


async def backfill_gaps(
    client,
    figi: str,
    gaps: list[tuple[date, date]],
    db: str,
) -> int:
    """Fetch missing bars from the broker for every entry in ``gaps``.

    ``client`` is any object exposing ``async get_candles(*, figi,
    date_from, date_to, interval="CANDLE_INTERVAL_DAY") ->
    list[dict|object]`` — the same Protocol used by the daily guardian.

    For each ``(start, end)`` pair the broker is queried once for the
    half-open interval ``[start + 1, end]`` (we already have a bar on
    ``start``). Candles whose date is today (the in-progress live bar
    that Tinkoff occasionally returns) are dropped. Writes go through
    ``replace_bars_for_figi(replace=False)`` so the call is purely
    additive — pre-existing bars outside the gap are preserved, and
    the ``UNIQUE(figi, ts)`` constraint makes duplicate inserts a
    silent no-op (``INSERT OR IGNORE``).

    Returns 0 immediately when ``gaps`` is empty (no broker call).
    """
    if not gaps:
        return 0
    today = date.today()
    total_added = 0
    for start, end in gaps:
        from_date = (start + timedelta(days=1)).isoformat()
        to_date = end.isoformat()
        candles = await client.get_candles(
            figi=figi,
            date_from=from_date,
            date_to=to_date,
            interval="CANDLE_INTERVAL_DAY",
        )
        closed = [c for c in candles if (d := _candle_date(c)) is not None and d < today]
        # Issue #3: clear the exhausted marker BEFORE replace_bars_for_figi
        # runs. That helper overwrites ``last_run_status`` to 'ok' in the
        # same transaction that inserts the bars, so we have to read and
        # clear the sentinel before it would be clobbered. Only do this
        # when we have something to write — a broker returning zero
        # candles must NOT clear the marker (would silently hide a real
        # upstream outage).
        prev = reset_exhausted_marker(db, figi) if closed else None
        added = replace_bars_for_figi(db, figi, closed, replace=False)
        total_added += added
        if added:
            _log.info(
                "guardian.completeness.gap_filled",
                extra={"figi": figi, "start": str(start), "end": str(end), "added": added},
            )
            if prev is not None:
                _log.info(
                    "guardian.completeness.exhausted_marker_cleared",
                    extra={"figi": figi, "previous_status": prev},
                )
    return total_added


def _is_completeness_exhausted(db: str, figi: str) -> bool:
    """Return True if this figi has been marked ``completeness_exhausted``.

    The marker lives on ``instrument_metadata.last_run_status`` —
    same pattern as ``recovery._is_exhausted`` — rather than in the
    ``pipeline`` table (which is append-only and would lose history
    on re-mark). The status column is small and indexed implicitly
    via the primary key lookup, so the check is a single row fetch.
    """
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "SELECT last_run_status FROM instrument_metadata WHERE figi = ?",
            (figi,),
        ).fetchone()
        return bool(row and row[0] == "completeness_exhausted")
    finally:
        con.close()


def _mark_completeness_exhausted(db: str, figi: str) -> None:
    """Persist the ``completeness_exhausted`` status for this figi.

    The guardian is the only writer of this status; the next pass
    will skip the figi entirely until the operator investigates and
    clears the marker (or until the broker grows the missing range).
    """
    con = sqlite3.connect(db)
    try:
        con.execute(
            "UPDATE instrument_metadata SET last_run_status = 'completeness_exhausted' "
            "WHERE figi = ?",
            (figi,),
        )
        con.commit()
    finally:
        con.close()


# Issue #3: sentinel values written by the two guardian passes.
# Centralised so callers don't hard-code the spellings and so a typo
# in any one location surfaces as a sentinel mismatch on the next write.
EXHAUSTED_STATUSES = frozenset({"completeness_exhausted", "stale_recovery_exhausted"})


def reset_exhausted_marker(db: str, figi: str) -> str | None:
    """Clear an exhausted marker on ``figi`` and return the previous status.

    Returns the previous status (``completeness_exhausted`` or
    ``stale_recovery_exhausted``) when a marker was cleared, or
    ``None`` if the row had no exhausted marker (or no row at all).

    Called automatically by ``backfill_gaps`` and ``BackfillRunner``
    on a successful write so a previously-exhausted figi resumes
    normal processing on the next guardian cycle. Also exposed via
    ``POST /api/admin/data-quality/reset-exhausted/{symbol}`` as the
    operator escape hatch.

    IMPORTANT sequencing note: ``replace_bars_for_figi`` overwrites
    ``last_run_status`` to ``'ok'`` in the SAME transaction as the
    bars insert. This helper MUST therefore run BEFORE that call —
    if it runs after, the sentinel has already been clobbered and
    the helper sees a non-exhausted row. Issue #3's root constraint.
    """
    placeholders = ",".join("?" for _ in EXHAUSTED_STATUSES)
    con = sqlite3.connect(db)
    try:
        try:
            prev_row = con.execute(
                f"SELECT last_run_status FROM instrument_metadata "
                f"WHERE figi = ? AND last_run_status IN ({placeholders})",
                (figi, *EXHAUSTED_STATUSES),
            ).fetchone()
        except sqlite3.OperationalError:
            # instrument_metadata table doesn't exist yet (pre-migration-004).
            return None
        if prev_row is None:
            return None
        con.execute(
            "UPDATE instrument_metadata SET last_run_status = 'ready' WHERE figi = ?",
            (figi,),
        )
        con.commit()
        return prev_row[0]
    finally:
        con.close()


async def run_completeness_pass(
    db: str,
    client,
    runner,  # accepted for signature parity with the daily guardian; unused.
    reports: dict[str, HealthReport],
) -> CompletenessSummary:
    """Walk every figi with ``INCOMPLETE_HISTORY`` and backfill its gaps.

    Skips figis already marked ``completeness_exhausted`` (the
    broker previously returned zero bars for every gap; no point
    asking again). A figi whose current pass also returned zero
    bars across all gaps is the trigger for the exhaustion marker.

    ``runner`` is accepted but unused — the completeness pass talks
    directly to the broker (one call per gap, sequential) rather
    than going through the ``BackfillRunner``. The argument is kept
    so ``run_daily_guardian`` can pass it through without branching.
    """
    del runner  # explicitly unused; documented above.

    summary = CompletenessSummary()
    for figi, report in reports.items():
        if HealthIssue.INCOMPLETE_HISTORY not in report.issues:
            continue
        summary.figis_examined += 1
        if _is_completeness_exhausted(db, figi):
            # already exhausted on a prior pass — don't re-query the broker.
            continue
        gaps = find_gap_intervals(db, figi, min_gap_days=5)
        summary.gaps_found += len(gaps)
        added = await backfill_gaps(client, figi, gaps, db)
        summary.bars_added += added
        if gaps and added == 0:
            _mark_completeness_exhausted(db, figi)
            summary.exhausted += 1
    return summary
