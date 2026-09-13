"""Per-ticker data quality drill-down.

Returns the HealthReport for the requested symbol (ticker or
figi). The endpoint is read-only; the daily guardian does the
recovery work and writes nothing here.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException

from ..data_quality.completeness import reset_exhausted_marker
from ..data_quality.health import HealthReport, compute_health
from ..db.bars_sqlite import resolve_figi_for_ticker
from ..observability.logging import get_logger
from .data_reads import _get_sqlite_path


router = APIRouter(prefix="/api", tags=["data-quality"])
logger = get_logger("algotrader_api.data_quality_route")


def _report_to_dict(report: HealthReport) -> dict[str, Any]:
    return {
        "figi": report.figi,
        "ticker": report.ticker,
        "health_score": report.health_score,
        # `HealthIssue` is `str, Enum` so `str(i)` yields the enum's
        # *class* repr (`HealthIssue.MISSING_RECENT`). Use the `value`
        # so the wire format matches the spec (`missing-recent-days`).
        "issues": [i.value for i in report.issues],
        "first_bar": report.first_bar.isoformat() if report.first_bar else None,
        "last_bar": report.last_bar.isoformat() if report.last_bar else None,
        "actual_bars": report.actual_bars,
        "expected_bars": report.expected_bars,
        "recent_gaps": [d.isoformat() for d in report.recent_gaps],
        "recent_failures": report.recent_failures,
    }


def _resolve_figi(sqlite_path: str, symbol: str) -> str | None:
    """Look up figi by ticker; fall back to figi equality."""
    figi = resolve_figi_for_ticker(sqlite_path, symbol)
    if figi:
        return figi
    con = sqlite3.connect(sqlite_path)
    try:
        row = con.execute(
            "SELECT figi FROM instruments WHERE figi = ?", (symbol,)
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


@router.get("/data-quality/{symbol}")
def get_data_quality(symbol: str) -> dict:
    """Return the full HealthReport for the requested symbol."""
    sqlite_path = _get_sqlite_path()
    figi = _resolve_figi(sqlite_path, symbol)
    if not figi:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_symbol", "symbol": symbol},
        )
    report = compute_health(sqlite_path, figi)
    return _report_to_dict(report)


@router.post("/admin/data-quality/reset-exhausted/{symbol}")
def reset_exhausted(symbol: str) -> dict:
    """Issue #3 operator escape hatch.

    Clears the ``completeness_exhausted`` or ``stale_recovery_exhausted``
    marker on ``symbol`` so the next guardian cycle processes it again.
    Successful broker fetches auto-clear the marker (see
    ``data_quality.completeness.backfill_gaps``); this endpoint is for
    the case where the operator has fixed the upstream cause (broker
    outage, misconfigured filter, etc.) and wants to force re-processing
    without waiting for a successful fetch.

    Returns the previous status so the caller can confirm what was
    cleared, or 404 if the figi has no exhausted marker to clear.
    """
    sqlite_path = _get_sqlite_path()
    figi = _resolve_figi(sqlite_path, symbol)
    if not figi:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_symbol", "symbol": symbol},
        )
    previous = reset_exhausted_marker(sqlite_path, figi)
    if previous is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "no_exhausted_marker",
                "figi": figi,
                "message": "no exhausted marker set on this figi — nothing to clear",
            },
        )
    logger.info(
        "admin.data_quality.exhausted_reset",
        figi=figi,
        previous_status=previous,
    )
    return {"figi": figi, "previous_status": previous, "status": "ready"}
