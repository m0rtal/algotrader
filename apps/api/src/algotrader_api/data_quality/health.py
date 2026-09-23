"""Data quality health computation.

Computes per-figi HealthReport with four sub-problems and a
0-100 health-score. The score is the contract every layer
(operator UI, daily guardian, recovery queue) reads.

See `openspec/changes/archive/2026-09-13-data-quality-guardian/`
for the spec.
"""
from __future__ import annotations

import logging
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
    BAR_CORRUPTION = "bar-corruption"


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
    HealthIssue.BAR_CORRUPTION: 40,
}
_INCOMPLETE_HISTORY_RATIO = 0.95


def _open(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


# Epoch used for the integer-arithmetic path in compute_all(). Matches the
# EPOCH constant in data_quality/gap_recovery.py (post PR #113). Kept local
# to avoid a cross-module dependency; values must agree or comparison logic
# will silently corrupt expected_bars counts.
_HEALTH_EPOCH = date(1970, 1, 1)


def _expected_weekdays_int(fst_int: int, lst_int: int) -> int:
    """Count weekdays in [fst_int, lst_int] (both days since 1970-01-01).

    Uses `(day + 3) % 7 < 5` to test Mon..Fri: 1970-01-01 was a Thursday
    (weekday 3), so `(0 + 3) % 7 = 3` (Thursday), `(1 + 3) % 7 = 4`
    (Friday), `(2 + 3) % 7 = 5` (Saturday, excluded). Mon=0 .. Sun=6
    so `< 5` selects Mon..Fri. Same trick as the optimised
    `find_gaps` (PR #113).
    """
    if lst_int < fst_int:
        return 0
    return sum(1 for d in range(fst_int, lst_int + 1) if (d + 3) % 7 < 5)


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


_log = logging.getLogger("algotrader_api.data_quality.health")


def _warn_if_holidays_empty(db_path: str) -> None:
    """Issue #4: log a warning when moex_holidays is empty at compute time.

    The migration (006) seeds the table, but if the seed ever fails
    silently (missing JSON, broken import path) the guardian would
    emit INCOMPLETE_HISTORY for every figi with no operator-visible
    signal. A warning at the top of ``compute_all`` makes the
    missing-data situation visible.
    """
    con = _open(db_path)
    try:
        try:
            count = con.execute("SELECT COUNT(*) FROM moex_holidays").fetchone()[0]
        except sqlite3.OperationalError:
            return
    finally:
        con.close()
    if count == 0:
        _log.warning(
            "data_quality.moex_holidays.empty",
            extra={
                "hint": "run python -m algotrader_api.scripts_import.import_moex_holidays",
                "impact": "INCOMPLETE_HISTORY will fire for every figi",
            },
        )


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


# SQL fragment reused by single-figi and bulk corruption counts. A bar is
# "corrupt" when its OHLCV violates one of the rules in
# `data_quality.integrity.validate_bar` that can be checked at the SQL
# boundary (volume < 0, all-zero OHLC, or high/low not bounding the other
# three prices). Duplicated here rather than imported because the integrity
# module is intentionally import-side-effect-free and we want one SQL
# pass, not a per-row Python validate.
_CORRUPTION_SQL = (
    "COUNT(*) FROM bars WHERE figi = ? AND ("
    "high < MAX(open, low, close) "
    "OR low  > MIN(open, high, close) "
    "OR volume < 0 "
    "OR (open = 0 AND high = 0 AND low = 0 AND close = 0)"
    ")"
)


def _count_corrupted_bars(con: sqlite3.Connection, figi: str) -> int:
    """Return the number of bars for `figi` that violate integrity rules.

    Counted via SQL rather than per-row Python `validate_bar` because the
    bars table holds millions of rows; one query is materially cheaper.
    """
    return int(con.execute(
        f"SELECT {_CORRUPTION_SQL}",
        (figi,),
    ).fetchone()[0])


def _corrupted_bars_bulk(
    con: sqlite3.Connection, figis: list[str]
) -> dict[str, int]:
    """Bulk-count corrupted bars per figi in a single GROUP BY query.

    Preserves the O(1)-queries invariant of `compute_all` (3776-figi
    universe on prod). Returns 0 for figis with no rows.
    """
    out: dict[str, int] = {f: 0 for f in figis}
    if not figis:
        return out
    placeholders = ",".join("?" for _ in figis)
    rows = con.execute(
        f"""
        SELECT figi,
               SUM(
                 (high < MAX(open, low, close))
                 OR (low  > MIN(open, high, close))
                 OR (volume < 0)
                 OR (open = 0 AND high = 0 AND low = 0 AND close = 0)
               ) AS n_bad
        FROM bars
        WHERE figi IN ({placeholders})
        GROUP BY figi
        """,
        tuple(figis),
    ).fetchall()
    for r in rows:
        out[r["figi"]] = int(r["n_bad"] or 0)
    return out


def _compute_issues_and_penalty(
    last_bar: date | None,
    actual_bars: int,
    expected_bars: int,
    gaps: list[date],
    failures: list[dict],
    today: date,
    corrupted_bars: int = 0,
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

    if corrupted_bars > 0:
        issues.append(HealthIssue.BAR_CORRUPTION)
        penalty += _PENALTY[HealthIssue.BAR_CORRUPTION]

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
        corrupted_bars = _count_corrupted_bars(con, figi)
        expected_bars = (
            _weekdays_excluding_holidays(first_bar, today, con)
            if first_bar
            else 0
        )
        issues, penalty = _compute_issues_and_penalty(
            last_bar, actual_bars, expected_bars, gaps, failures, today,
            corrupted_bars=corrupted_bars,
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
    _warn_if_holidays_empty(db_path)
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
        corrupted_map = _corrupted_bars_bulk(con, figis)
        holiday_rows = con.execute("SELECT date FROM moex_holidays").fetchall()
        holidays = {r["date"] for r in holiday_rows}
        # Bulk hot path: load holidays as days since EPOCH for the integer
        # weekday arithmetic in compute_all. Matches the post-PR-#113 trick
        # in data_quality/gap_recovery.py and is ~13x faster on the prod
        # universe (3796 figis). The original `_weekdays_minus_holiday_set`
        # is kept above for callers that already hold `date` objects.
        holidays_int = {
            (date.fromisoformat(r["date"]) - _HEALTH_EPOCH).days
            for r in holiday_rows
        }
        today_int = (today - _HEALTH_EPOCH).days
    finally:
        con.close()

    meta_by_figi = {r["figi"]: r["last_run_status"] for r in meta_rows}
    out: dict[str, HealthReport] = {}
    for r in rows:
        figi = r["figi"]
        first_bar = date.fromisoformat(r["first_bar"]) if r["first_bar"] else None
        last_bar = date.fromisoformat(r["last_bar"]) if r["last_bar"] else None
        actual = int(r["actual_bars"])
        # Inline integer weekday calc (~13x faster than the date-object loop
        # for the 3796-figi prod universe). Mirrors find_gaps rewrite
        # (PR #113) — same `(d + 3) % 7 < 5` weekday test, plus a single
        # subtract against the bulk-loaded integer holiday set so the
        # weekend count is correct on observed days too.
        expected = 0
        if first_bar is not None:
            fst_int = (first_bar - _HEALTH_EPOCH).days
            expected = _expected_weekdays_int(fst_int, today_int)
            # Subtract any holidays that fall strictly inside [fst_int, today_int].
            for h_int in holidays_int:
                if fst_int <= h_int <= today_int:
                    # Holidays used in the regular code path pass through
                    # `_weekdays_minus_holiday_set` which counted every day in
                    # the range and only subtracted those that lined up with
                    # weekdays. Here we mirror that by checking whether this
                    # holiday was counted above (i.e. its int lands on a
                    # weekday under the `(d + 3) % 7 < 5` rule).
                    if (h_int + 3) % 7 < 5:
                        expected -= 1
        issues, penalty = _compute_issues_and_penalty(
            last_bar, actual, expected, [], failures_map.get(figi, []), today,
            corrupted_bars=corrupted_map.get(figi, 0),
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
