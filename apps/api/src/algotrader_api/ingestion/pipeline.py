"""Pipeline phase lifecycle tracking via SQLite.

Each worker run inserts a row at start of each phase and updates at end.
The /api/pipeline route returns the latest row per phase.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from ..db.sqlite import execute, execute_returning_id
from ..observability.logging import get_logger

logger = get_logger("algotrader_api.ingestion.pipeline")

PhaseName = Literal["discover_universe", "fetch_bars"]
PhaseStatus = Literal["ok", "warn", "err", "idle"]


@dataclass
class PhaseResult:
    id: int
    phase: str
    started_at: str
    finished_at: str | None
    rows_processed: int
    status: str
    detail: str | None


def start_phase(db_path: str, phase: str) -> int:
    """Insert a new pipeline row in 'idle' status. Returns run id."""
    rid = execute_returning_id(
        db_path,
        "INSERT INTO pipeline (phase, started_at, status, rows_processed) VALUES (?, ?, ?, 0)",
        (phase, _now_iso(), "idle"),
    )
    logger.info("pipeline.phase.start", phase=phase, run_id=rid)
    return rid


def end_phase(
    db_path: str,
    run_id: int,
    *,
    status: PhaseStatus,
    rows_processed: int = 0,
    detail: str | None = None,
) -> None:
    """Update a pipeline row to mark it complete."""
    execute(
        db_path,
        "UPDATE pipeline SET finished_at = ?, status = ?, rows_processed = ?, detail = ? WHERE id = ?",
        (_now_iso(), status, rows_processed, detail, run_id),
    )
    logger.info(
        "pipeline.phase.end",
        phase="<from row>",
        run_id=run_id,
        status=status,
        rows_processed=rows_processed,
    )


def latest_per_phase(db_path: str) -> list[PhaseResult]:
    """Return the latest row for each phase name, ordered by phase name.

    Uses MAX(id) (autoincrement primary key) instead of MAX(started_at) to
    correctly disambiguate rows inserted in the same second (which happens
    frequently for the manual fetch endpoint).
    """
    rows = execute(
        db_path,
        "SELECT p.* FROM pipeline p "
        "INNER JOIN ("
        "  SELECT phase, MAX(id) AS max_id FROM pipeline GROUP BY phase"
        ") latest ON p.phase = latest.phase AND p.id = latest.max_id "
        "ORDER BY p.phase",
    )
    return [
        PhaseResult(
            id=r["id"],
            phase=r["phase"],
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            rows_processed=r["rows_processed"],
            status=r["status"],
            detail=r["detail"],
        )
        for r in rows
    ]


def _now_iso() -> str:
    # Local time ISO format. Worker is one-shot, so UTC vs local is not critical.
    return time.strftime("%Y-%m-%dT%H:%M:%S")
