"""Pipeline status endpoint — GET /api/pipeline.

Returns the latest row per phase for the frontend Pipeline tab.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ..db import sqlite as sqlitedb
from ..ingestion import pipeline as pipeline_mod

router = APIRouter(prefix="/api", tags=["pipeline"])


class PhaseStatus(BaseModel):
    phase: Literal["discover_universe", "fetch_bars", "backfill_universe"]
    status: Literal["ok", "warn", "err", "idle"]
    startedAt: str
    finishedAt: str | None
    rowsProcessed: int
    detail: str | None


class PipelineResponse(BaseModel):
    phases: list[PhaseStatus]


def _get_sqlite_path() -> str:
    """Resolve from app state, fallback to configured settings path."""
    from ..config import get_settings

    return get_settings().sqlite_path


@router.get("/pipeline", response_model=PipelineResponse)
def get_pipeline() -> PipelineResponse:
    rows = pipeline_mod.latest_per_phase(_get_sqlite_path())
    phases = [
        PhaseStatus(
            phase=r.phase,  # type: ignore[arg-type]
            status=r.status,  # type: ignore[arg-type]
            startedAt=r.started_at,
            finishedAt=r.finished_at,
            rowsProcessed=r.rows_processed,
            detail=r.detail,
        )
        for r in rows
    ]
    return PipelineResponse(phases=phases)
