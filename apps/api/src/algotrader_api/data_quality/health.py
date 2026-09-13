"""Data quality health computation.

Computes per-figi HealthReport with four sub-problems and a
0-100 health-score. The score is the contract every layer
(operator UI, daily guardian, recovery queue) reads.

See `openspec/changes/archive/2026-09-13-data-quality-guardian/`
for the spec.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum

from ..domain.tradeable import TRADEABLE_CLASSES


class HealthIssue(str, Enum):
    MISSING_RECENT = "missing-recent-days"
    SPARSE_HISTORY = "sparse-history"
    HAS_GAPS = "has-gaps"
    RATE_LIMITED_FAILURES = "rate-limited-failures"
    INCOMPLETE_HISTORY = "incomplete-history"


@dataclass
class HealthReport:
    figi: str
    ticker: str | None
    health_score: int
    issues: list[HealthIssue] = field(default_factory=list)
    first_bar: date | None = None
    last_bar: date | None = None
    actual_bars: int = 0
    expected_bars: int = 0
    recent_gaps: list[date] = field(default_factory=list)
    recent_failures: list[dict] = field(default_factory=list)


_MISSING_RECENT_DAYS = 3
_GAP_MIN_DAYS = 5
_RATELIMIT_LOOKBACK_DAYS = 7
_SPARSE_RATIO = 0.9
_MAX_RECENT_GAPS = 5
_MAX_RECENT_FAILURES = 5
_PENALTY = {
    HealthIssue.MISSING_RECENT: 30,
    HealthIssue.SPARSE_HISTORY: 20,
    HealthIssue.HAS_GAPS: 20,
    HealthIssue.RATE_LIMITED_FAILURES: 30,
    HealthIssue.INCOMPLETE_HISTORY: 25,
}
_INCOMPLETE_HISTORY_RATIO = 0.95


def _open(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _meta_for_figi(con: sqlite3.Connection, figi: str) -> dict | None:
    """Return instrument metadata joined with instrument ticker for the figi."""
    row = con.execute(
        "SELECT m.last_bar_ts, m.total_bars, m.last_run_status, i.ticker "
        "FROM instruments i LEFT JOIN instrument_metadata m ON m.figi = i.figi "
        "WHERE i.figi = ?",
        (figi,),
    ).fetchone()
    return dict(row) if row else None


def _bars_range(con: sqlite3.Connection, figi: str) -> tuple[date | None, date | None, int]:
    row = con.execute(
        "SELECT MIN(ts), MAX(ts), COUNT(*) FROM bars WHERE figi = ?",
        (figi,),
    ).fetchone()
    if not row or row[0] is None:
        return None, None, 0
    first = date.fromisoformat(row[0])
    last = date.fromisoformat(row[1])
    return first, last, int(row[2])


def _weekdays_excluding_holidays(
    start: date, end: date, con: sqlite3.Connection
) -> int:
    """Count weekdays between start and end (inclusive), minus MOEX holidays in range.

    Holidays are loaded from the `moex_holidays` table created by migration 006.
    Returns 0 when end < start (defensive).
    """
    if end < start:  # pragma: no cover — defensive guard
        return 0
    weekdays = sum(
        1 for i in range((end - start).days + 1)
        if (start + timedelta(days=i)).weekday() < 5
    )
    rows = con.execute(
        "SELECT date FROM moex_holidays WHERE date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return weekdays - len(rows)


def _weekdays_minus_holiday_set(start: date, end: date, holidays: set[str]) -> int:
    """Same as ``_weekdays_excluding_holidays`` but takes a pre-loaded holiday set.

    Used by the bulk ``compute_all`` path which loads all holidays once
    instead of issuing one query per figi.
    """
    if end < start:  # pragma: no cover — defensive guard
        return 0
    weekdays = sum(
        1 for i in range((end - start).days + 1)
        if (start + timedelta(days=i)).weekday() < 5
    )
    # Count only holidays strictly inside [start, end].
    in_range = sum(
        1 for i in range((end - start).days + 1)
        if (start + timedelta(days=i)).isoformat() in holidays
    )
    return weekdays - in_range


def _detect_gaps(
    con: sqlite3.Connection, figi: str, max_keep: int = _MAX_RECENT_GAPS
) -> list[date]:
    """Return up to `max_keep` long gaps (> _GAP_MIN_DAYS) in figi's history.

    A gap is reported as the START date of the gap (the last bar
    before the missing range). For drill-down, the operator wants
    "when did we stop getting data"; the start-of-gap date is the
    actionable row.
    """
    rows = con.execute(
        "SELECT ts FROM bars WHERE figi = ? AND ts < date('now') ORDER BY ts",
        (figi,),
    ).fetchall()
    if len(rows) < 2:
        return []
    gaps: list[date] = []
    prev = date.fromisoformat(rows[0][0])
    for row in rows[1:]:
        cur = date.fromisoformat(row[0])
        diff = (cur - prev).days
        if diff > _GAP_MIN_DAYS:
            gaps.append(prev)
            if len(gaps) >= max_keep:
                break
        prev = cur
    return gaps


def _recent_failures(
    con: sqlite3.Connection, figi: str, max_keep: int = _MAX_RECENT_FAILURES
) -> list[dict]:
    rows = con.execute(
        "SELECT ts, level, message FROM ingestion_logs "
        "WHERE figi = ? AND ts > datetime('now', ? || ' days') "
        "AND level IN ('warn', 'error') "
        "AND (message LIKE '%rate%' OR message LIKE '%RESOURCE_EXHAUSTED%') "
        "ORDER BY id DESC LIMIT ?",
        (figi, f"-{_RATELIMIT_LOOKBACK_DAYS}", max_keep),
    ).fetchall()
    return [dict(r) for r in rows]


def _compute_issues_and_penalty(
    last_bar: date | None,
    actual_bars: int,
    expected_bars: int,
    gaps: list[date],
    failures: list[dict],
    today: date,
) -> tuple[list[HealthIssue], int]:
    issues: list[HealthIssue] = []
    penalty = 0

    if last_bar is None or last_bar < today - timedelta(days=_MISSING_RECENT_DAYS):
        issues.append(HealthIssue.MISSING_RECENT)
        penalty += _PENALTY[HealthIssue.MISSING_RECENT]

    if expected_bars > 0 and actual_bars < expected_bars * _SPARSE_RATIO:
        issues.append(HealthIssue.SPARSE_HISTORY)
        penalty += _PENALTY[HealthIssue.SPARSE_HISTORY]

    if (
        expected_bars > 0
        and actual_bars < expected_bars * _INCOMPLETE_HISTORY_RATIO
    ):
        issues.append(HealthIssue.INCOMPLETE_HISTORY)
        penalty += _PENALTY[HealthIssue.INCOMPLETE_HISTORY]

    if gaps:
        issues.append(HealthIssue.HAS_GAPS)
        penalty += _PENALTY[HealthIssue.HAS_GAPS]

    if failures:
        issues.append(HealthIssue.RATE_LIMITED_FAILURES)
        penalty += _PENALTY[HealthIssue.RATE_LIMITED_FAILURES]

    return issues, penalty


def compute_health(
    db_path: str, figi: str, today: date | None = None
) -> HealthReport:
    """Compute the HealthReport for a single figi."""
    today = today or date.today()
    con = _open(db_path)
    try:
        meta = _meta_for_figi(con, figi)
        if meta is None:
            # No instrument row at all — treat as full health deficit
            return HealthReport(
                figi=figi, ticker=None, health_score=0,
                issues=[HealthIssue.MISSING_RECENT],
            )
        first_bar, last_bar, actual_bars = _bars_range(con, figi)
        gaps = _detect_gaps(con, figi)
        failures = _recent_failures(con, figi)
        expected_bars = (
            _weekdays_excluding_holidays(first_bar, today, con)
            if first_bar
            else 0
        )
        issues, penalty = _compute_issues_and_penalty(
            last_bar, actual_bars, expected_bars, gaps, failures, today
        )
        score = max(0, 100 - penalty)
        return HealthReport(
            figi=figi,
            ticker=meta.get("ticker"),
            health_score=score,
            issues=issues,
            first_bar=first_bar,
            last_bar=last_bar,
            actual_bars=actual_bars,
            expected_bars=expected_bars,
            recent_gaps=gaps,
            recent_failures=failures,
        )
    finally:
        con.close()


def compute_all(db_path: str, today: date | None = None) -> dict[str, HealthReport]:
    """Compute HealthReport for every tradeable figi in `instruments`.

    Batches the three per-figi queries (`bars`, `ingestion_logs`,
    `instrument_metadata` join) into single queries and walks the
    result in Python. This keeps `/pending` and the daily guardian
    O(N) SQLite queries instead of O(N * 3) and avoids 10000+
    query dispatches on a 3700-figi universe.

    Non-tradeable rows (e.g. future/option) are excluded at the
    SQL boundary per the TRADEABLE_CLASSES contract.
    """
    today = today or date.today()
    placeholders = ",".join("?" for _ in TRADEABLE_CLASSES)
    con = _open(db_path)
    try:
        # One query: bars aggregates + metadata join.
        rows = con.execute(
            f"""
            SELECT
                i.figi AS figi,
                i.ticker AS ticker,
                COALESCE(b.first_bar, NULL) AS first_bar,
                COALESCE(b.last_bar,  NULL) AS last_bar,
                COALESCE(b.actual,    0)    AS actual_bars
            FROM instruments i
            LEFT JOIN (
                SELECT figi, MIN(ts) AS first_bar, MAX(ts) AS last_bar, COUNT(*) AS actual
                FROM bars GROUP BY figi
            ) b ON b.figi = i.figi
            WHERE i.class IN ({placeholders})
            """,
            tuple(TRADEABLE_CLASSES),
        ).fetchall()
        meta_rows = con.execute(
            "SELECT figi, last_run_status FROM instrument_metadata"
        ).fetchall()
        figis = [r["figi"] for r in rows]
        failures_map = _recent_failures_bulk(con, figis, today)
        holiday_rows = con.execute("SELECT date FROM moex_holidays").fetchall()
        holidays = {r["date"] for r in holiday_rows}
    finally:
        con.close()

    meta_by_figi = {r["figi"]: r["last_run_status"] for r in meta_rows}
    out: dict[str, HealthReport] = {}
    for r in rows:
        figi = r["figi"]
        first_bar = date.fromisoformat(r["first_bar"]) if r["first_bar"] else None
        last_bar = date.fromisoformat(r["last_bar"]) if r["last_bar"] else None
        actual = int(r["actual_bars"])
        expected = (
            _weekdays_minus_holiday_set(first_bar, today, holidays)
            if first_bar
            else 0
        )
        issues, penalty = _compute_issues_and_penalty(
            last_bar, actual, expected, [], failures_map.get(figi, []), today
        )
        out[figi] = HealthReport(
            figi=figi,
            ticker=r["ticker"],
            health_score=max(0, 100 - penalty),
            issues=issues,
            first_bar=first_bar,
            last_bar=last_bar,
            actual_bars=actual,
            expected_bars=expected,
            recent_gaps=[],  # gaps are intentionally not scanned on the
                              # 3776-figi bulk path; per-figi `compute_health`
                              # still returns them for the drill-down endpoint.
            recent_failures=failures_map.get(figi, []),
        )
    return out


def _recent_failures_bulk(
    con: sqlite3.Connection, figis: list[str], today: date, max_keep: int = _MAX_RECENT_FAILURES
) -> dict[str, list[dict]]:
    """Bulk-load recent rate-limited failures for many figis at once.

    Returns a map `figi -> [rows...]`. Rows are capped at `max_keep`
    per figi, ordered most-recent first.
    """
    if not figis:
        return {}
    placeholders = ",".join("?" for _ in figis)
    # We don't have window functions in the SQLite shipped with the
    # project; use a correlated subquery to rank within each figi.
    rows = con.execute(
        f"""
        SELECT figi, ts, level, message FROM (
            SELECT figi, ts, level, message,
                   ROW_NUMBER() OVER (PARTITION BY figi ORDER BY id DESC) AS rn
            FROM ingestion_logs
            WHERE figi IN ({placeholders})
              AND ts > datetime('now', ? || ' days')
              AND level IN ('warn', 'error')
              AND (message LIKE '%rate%' OR message LIKE '%RESOURCE_EXHAUSTED%')
        ) WHERE rn <= ?
        """,
        (*figis, f"-{_RATELIMIT_LOOKBACK_DAYS}", max_keep),
    ).fetchall()
    out: dict[str, list[dict]] = {f: [] for f in figis}
    for r in rows:
        out.setdefault(r["figi"], []).append(  # pragma: no cover — defensive
            {"ts": r["ts"], "level": r["level"], "message": r["message"]}
        )
    return out
