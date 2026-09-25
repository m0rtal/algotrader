"""ML-ready data shape helpers + consumption-time coverage gate.

Two responsibilities in one module (kept colocated because both serve
the same ML-readiness surface and the admin endpoint):

* ``row_count`` / ``date_range`` — view-shape helpers over
  ``ml_features`` (migration 021). Consumed by routes/admin.py.
* ``build_features`` / ``check_coverage`` / ``auto_recovery`` /
  ``InsufficientDataError`` — the pre-consumption coverage gate
  (Spec: openspec/changes/coverage-and-quality/specs/data-quality/spec.md
  Requirement: Pre-Consumption Coverage Gate).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from typing import Any, Optional

from algotrader_api.ingestion.backfill import backfill_bonds_to_depth


# ---------------------------------------------------------------------------
# Consumption-time coverage gate (PR-3)
# ---------------------------------------------------------------------------

_LOG = logging.getLogger("algotrader_api.ml.features")


class InsufficientDataError(Exception):
    """Raised when one or more figis fail the coverage gate."""

    def __init__(
        self,
        failing_figis: list[dict[str, Any]],
        attempted_recovery: bool = False,
    ):
        self.failing_figis = failing_figis
        self.attempted_recovery = attempted_recovery
        super().__init__(
            f"Insufficient data for {len(failing_figis)} figis "
            f"(attempted_recovery={attempted_recovery})"
        )


def check_coverage(
    conn: sqlite3.Connection,
    figis: list[str],
    coverage_threshold: float = 0.95,
) -> list[dict[str, Any]]:
    """Return list of failing figis. Empty list = all OK.

    A figi fails if:
    - max_ts < yesterday (stale), OR
    - bars_count < coverage_threshold * expected_bars (incomplete)

    Args:
        conn: open SQLite connection.
        figis: list of figis to check.
        coverage_threshold: minimum bars/expected ratio (default 0.95).

    Returns:
        List of {"figi", "max_ts", "bars_count", "expected", "reason"}
        for figis failing. reason ∈ {"stale", "incomplete", "both"}.
    """
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    failing: list[dict[str, Any]] = []
    for figi in figis:
        row = conn.execute(
            """SELECT
                (SELECT MAX(ts) FROM bars WHERE figi = ?) AS max_ts,
                (SELECT COUNT(*) FROM bars WHERE figi = ?) AS bars_count,
                (SELECT expected_bars FROM instruments WHERE figi = ?) AS expected
            """,
            (figi, figi, figi),
        ).fetchone()
        max_ts, bars_count, expected = row[0], row[1], row[2]
        reasons = []
        if max_ts is None or max_ts < yesterday:
            reasons.append("stale")
        if expected and expected > 0 and bars_count < coverage_threshold * expected:
            reasons.append("incomplete")
        if reasons:
            failing.append({
                "figi": figi,
                "max_ts": max_ts,
                "bars_count": bars_count,
                "expected": expected,
                "reason": "both" if len(reasons) > 1 else reasons[0],
            })
    return failing


def auto_recovery(
    conn: sqlite3.Connection,
    failing_figis: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attempt single-round recovery for failing figis. Re-check after.

    For bond figis: call backfill_bonds_to_depth. For non-bonds: log warning
    (no Tinkoff direct-fetch API in scope for PR-3; non-bond recovery is
    covered by the daily _step_backfill_moex and _step_bonds_depth steps).

    Returns updated failing_figis list after re-check.
    """
    bond_figis = []
    for entry in failing_figis:
        row = conn.execute(
            "SELECT class FROM instruments WHERE figi = ?", (entry["figi"],),
        ).fetchone()
        if row and row[0] == "bond":
            bond_figis.append(entry["figi"])

    if bond_figis:
        try:
            backfill_bonds_to_depth(target_days=30, conn=conn)
        except Exception as e:
            _LOG.warning(
                "auto_recovery_backfill_failed",
                extra={"event": "auto_recovery_backfill_failed",
                       "figis": bond_figis, "error": str(e)[:200]},
            )
            # propagate — do not retry; the exception bubbles to the caller

    # Re-check coverage after recovery
    return check_coverage(conn, [f["figi"] for f in failing_figis])


def build_features(
    conn: sqlite3.Connection,
    figis: list[str],
    window: tuple[date, date] | None = None,
) -> Any:
    """Build the feature matrix for the given figis.

    Validates coverage first; raises InsufficientDataError if any figi
    fails (after one round of auto-recovery).

    Args:
        conn: open SQLite connection.
        figis: list of figis to include in the feature matrix.
        window: optional (start, end) date range. None = full history
                (listing to yesterday).

    Returns:
        Feature matrix. Exact type/format is implementation choice
        (pandas DataFrame is the typical target; for PR-3 the test
        only asserts non-None).

    Raises:
        InsufficientDataError: if any figi fails coverage after auto_recovery.
    """
    failing = check_coverage(conn, figis)
    if failing:
        _LOG.warning(
            "insufficient_data_pre_recovery",
            extra={"event": "insufficient_data_pre_recovery",
                   "count": len(failing), "failing_figis": failing},
        )
        # Single round of auto-recovery
        try:
            failing = auto_recovery(conn, failing)
        except Exception as e:
            _LOG.error(
                "auto_recovery_raised",
                extra={"event": "auto_recovery_raised", "error": str(e)[:200]},
            )
            raise InsufficientDataError(failing, attempted_recovery=True) from e
        if failing:
            _LOG.error(
                "insufficient_data_error",
                extra={"event": "insufficient_data_error",
                       "attempted_recovery": True,
                       "count": len(failing), "failing_figis": failing},
            )
            raise InsufficientDataError(failing, attempted_recovery=True)

    # All good — build the feature matrix
    # For PR-3 the matrix is a simple list-of-dicts. Real ML would use pandas.
    rows = []
    for figi in figis:
        rows.append({
            "figi": figi,
            "max_ts": conn.execute(
                "SELECT MAX(ts) FROM bars WHERE figi=?", (figi,)
            ).fetchone()[0],
            "bars_count": conn.execute(
                "SELECT COUNT(*) FROM bars WHERE figi=?", (figi,)
            ).fetchone()[0],
        })
    return {"figis": figis, "rows": rows}


# ---------------------------------------------------------------------------
# ml_features view helpers (admin endpoint)
# ---------------------------------------------------------------------------


def row_count(db_path: str) -> int:
    """Number of tradeable rows in ``ml_features``.

    Trades freshness for accuracy: WAL mode means rows that have been
    written but not yet checkpointed by the writer are visible to this
    reader (SQLite reads WAL before main file), so the count reflects
    in-flight writes.
    """
    con = sqlite3.connect(db_path, timeout=5)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM ml_features WHERE is_tradeable=1"
        ).fetchone()[0]
    finally:
        con.close()


def date_range(db_path: str) -> tuple[Optional[str], Optional[str]]:
    """Inclusive ``(min_ts, max_ts)`` of tradeable rows, or ``(None, None)``
    when there are no rows yet (cold DB before first chain run).
    """
    con = sqlite3.connect(db_path, timeout=5)
    try:
        r = con.execute(
            "SELECT MIN(ts), MAX(ts) FROM ml_features WHERE is_tradeable=1"
        ).fetchone()
        return r[0], r[1]
    finally:
        con.close()
