"""Freshness checks for the dividends pipeline + cross-domain pipeline guard.

Two public entry points:

- ``dividends_freshness_check(db_path, stale_threshold_days=7)`` — used
  inside the dividends step when 0 rows land in a run. Returns a
  ``FreshnessReport``; raises ``AssertionError`` when the dividends
  table is empty or its most-recent row is older than the threshold.

- ``pipeline_freshness_check(db_path, max_chain_age_hours=24)`` —
  pipeline-level sanity gate. Reads the most-recent timestamps for
  bars, dividends, and corporate actions; raises ``AssertionError``
  when any of them is older than the threshold OR when any of the
  underlying tables is missing/empty.

Table-existence uses ``sqlite_master`` lookups so that the checks
gracefully skip on a fresh DB (instead of crashing on
``no such table:``).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class FreshnessReport:
    """Snapshot of how stale a single domain is."""

    domain: str
    last_fetch_at: str | None
    age_hours: float | None
    is_stale: bool
    row_count: int

    def as_dict(self) -> dict:
        return {
            "last_fetch_at": self.last_fetch_at,
            "age_hours": self.age_hours,
            "is_stale": self.is_stale,
            "row_count": self.row_count,
        }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_hours(ts: str | None, *, now: datetime | None = None) -> float | None:
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    return round((_now_utc() - parsed).total_seconds() / 3600.0, 2)


def dividends_freshness_check(
    db_path: str, stale_threshold_days: int = 7
) -> FreshnessReport:
    """Verify the dividends table is recent enough to be trustworthy.

    Returns a :class:`FreshnessReport`. Raises :class:`AssertionError`
    when the table is missing, empty, or its most-recent row is older
    than ``stale_threshold_days``.
    """
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        if not _table_exists(conn, "dividends"):
            raise AssertionError("dividends table missing")
        row = conn.execute(
            "SELECT COUNT(*), MAX(retrieved_at) FROM dividends"
        ).fetchone()
        count = int(row[0] or 0)
        last_ts = row[1]
        age_h = _age_hours(last_ts)
        is_stale = (
            count == 0
            or age_h is None
            or age_h > stale_threshold_days * 24
        )
        report = FreshnessReport(
            domain="dividends",
            last_fetch_at=last_ts,
            age_hours=age_h,
            is_stale=is_stale,
            row_count=count,
        )
        if is_stale:
            raise AssertionError(
                f"dividends stale: count={count} last={last_ts} "
                f"age_hours={age_h} threshold_days={stale_threshold_days}"
            )
        return report
    finally:
        conn.close()


def _domain_timestamp(
    conn: sqlite3.Connection, table: str, column: str
) -> tuple[int, str | None]:
    """Return (row_count, max(timestamp)) for a table; (0, None) if missing."""
    if not _table_exists(conn, table):
        return 0, None
    row = conn.execute(
        f"SELECT COUNT(*), MAX({column}) FROM {table}"  # noqa: S608 — table/column whitelisted by caller
    ).fetchone()
    return int(row[0] or 0), row[1]


def pipeline_freshness_check(db_path: str, max_chain_age_hours: int = 24) -> dict:
    """Pipeline-level freshness assertion.

    Raises :class:`AssertionError` when any of the watched domains
    (bars_adjusted, dividends, corporate_actions) is empty OR its
    most-recent timestamp is older than ``max_chain_age_hours``. Also
    raises when ``pipeline_log`` is empty (cron never ran).
    """
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        bars_count, bars_last = _domain_timestamp(
            conn, "bars_adjusted", "computed_at"
        )
        div_count, div_last = _domain_timestamp(
            conn, "dividends", "retrieved_at"
        )
        ca_count, ca_last = _domain_timestamp(
            conn, "corporate_actions", "retrieved_at"
        )

        if not _table_exists(conn, "pipeline_log"):
            raise AssertionError("pipeline_log table missing")
        log_row = conn.execute(
            "SELECT MAX(finished_at) FROM pipeline_log WHERE result='ok'"
        ).fetchone()
        last_chain_ts = log_row[0] if log_row else None
        last_chain_age = _age_hours(last_chain_ts)

        result = {
            "bars": {
                "last_fetch_at": bars_last,
                "age_hours": _age_hours(bars_last),
                "is_stale": bars_count == 0
                or (_age_hours(bars_last) or 0) > max_chain_age_hours,
            },
            "dividends": {
                "last_fetch_at": div_last,
                "age_hours": _age_hours(div_last),
                "is_stale": div_count == 0
                or (_age_hours(div_last) or 0) > max_chain_age_hours,
            },
            "corporate_actions": {
                "last_fetch_at": ca_last,
                "age_hours": _age_hours(ca_last),
                "is_stale": ca_count == 0
                or (_age_hours(ca_last) or 0) > max_chain_age_hours,
            },
            "last_chain_at": last_chain_ts,
            "last_chain_age_hours": last_chain_age,
        }

        failures: list[str] = []
        for domain, payload in result.items():
            if isinstance(payload, dict) and payload.get("is_stale"):
                failures.append(domain)

        if last_chain_age is None or last_chain_age > max_chain_age_hours:
            failures.append("pipeline_log")

        if failures:
            raise AssertionError(
                f"pipeline stale in: {', '.join(failures)} "
                f"(max_age_hours={max_chain_age_hours})"
            )

        return result
    finally:
        conn.close()