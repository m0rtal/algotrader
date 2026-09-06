"""Admin routes — POST /api/admin/fetch for manual worker trigger.

LAN-bind only. Returns 202 immediately with run_id, then runs worker phases
as a background task.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, HTTPException

from ..config import get_settings
from ..db import sqlite as sqlitedb
from ..db.sqlite import execute_returning_id  # noqa: F401 — kept for compat
from ..observability.logging import get_logger
from ..routes.settings import _get_sqlite_path  # reuse the holder

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = get_logger("algotrader_api.admin")

# Track running tasks to avoid concurrent runs on the same process
_running: set[asyncio.Task] = set()


def _is_disabled() -> bool:
    settings = get_settings()
    return bool(settings.fetch_disabled) or os.environ.get("ALGOTRADER_FETCH_DISABLED") == "1"


@router.post("/fetch", status_code=202)
async def trigger_fetch() -> dict:
    """Manually trigger a fetch run. Returns run_id for tracking."""
    if _is_disabled():
        raise HTTPException(
            status_code=503,
            detail={"error": "fetch_disabled", "message": "fetch is disabled by configuration"},
        )

    # Generate run id by inserting a discover_universe phase row first
    db_path = _get_sqlite_path()
    from ..ingestion import pipeline as pipeline_mod

    run_id = pipeline_mod.start_phase(db_path, "discover_universe")

    task = asyncio.create_task(_run_phases(run_id, db_path))
    _running.add(task)
    task.add_done_callback(_running.discard)

    return {"run_id": run_id, "started_at": _now_iso()}


async def _run_phases(run_id: int, db_path: str) -> None:
    """Background task: run universe + bars phases against the live client."""
    from ..config import get_settings
    from ..ingestion import (
        bars as bars_mod,
        client as client_mod,
        pipeline as pipeline_mod,
        rate_limit,
        retry,
        universe,
    )

    settings = get_settings()
    try:
        client = client_mod.make_client()
    except RuntimeError as e:
        logger.error("admin.client.failed", error=str(e))
        pipeline_mod.end_phase(db_path, run_id, status="err", detail=str(e))
        return

    rate_limiter = rate_limit.RateLimiter()
    retry_policy = retry.AdaptiveRetry()

    try:
        # Phase 1: discover universe
        rows = await universe.discover_universe(client)
        universe.upsert_instruments(db_path, rows)
        pipeline_mod.end_phase(db_path, run_id, status="ok", rows_processed=len(rows))

        # Phase 2: fetch bars
        bars_run = pipeline_mod.start_phase(db_path, "fetch_bars")
        instruments_rows = sqlitedb.execute(
            db_path, "SELECT ticker, figi, class FROM instruments"
        )
        instruments = [dict(r) for r in instruments_rows]
        total_rows, rate_hits = await bars_mod.run_bars_phase(
            client,
            instruments=instruments,
            bars_dir=settings.bars_dir,
            history_years=settings.history_years,
            rate_limiter=rate_limiter,
            retry_policy=retry_policy,
        )
        pipeline_mod.end_phase(
            db_path,
            bars_run,
            status="ok",
            rows_processed=total_rows,
            detail=f"rate_limit_hits={rate_hits}",
        )
    except Exception as e:
        logger.error("admin.run.failed", error=str(e))
        pipeline_mod.end_phase(db_path, run_id, status="err", detail=str(e))
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat()
