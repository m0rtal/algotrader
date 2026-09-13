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
}


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


def _weekdays_between(start: date, end: date) -> int:
    """Count business days between start and end inclusive."""
    if end < start:
        return 0
    n = 0
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # Mon-Fri
            n += 1
        cur += timedelta(days=1)
    return n


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
            _weekdays_between(first_bar, today) if first_bar else 0
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

    Non-tradeable rows (e.g. future/option) are excluded at the
    SQL boundary per the TRADEABLE_CLASSES contract.
    """
    placeholders = ",".join("?" for _ in TRADEABLE_CLASSES)
    con = _open(db_path)
    try:
        rows = con.execute(
            f"SELECT figi FROM instruments WHERE class IN ({placeholders})",
            tuple(TRADEABLE_CLASSES),
        ).fetchall()
    finally:
        con.close()
    return {r["figi"]: compute_health(db_path, r["figi"], today) for r in rows}
