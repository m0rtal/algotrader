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


@router.get("/fetch/status")
async def fetch_status() -> dict:
    """Quick health probe: is the broker token set in DB?

    No Tinkoff call is made. Safe to poll. Used by the Settings page to decide
    whether the 'Run fetch' button is enabled.
    """
    from ..db.secrets import get_broker_token as _get_token

    db_path = _get_sqlite_path()
    token = _get_token(db_path)

    return {
        "fetch_disabled": _is_disabled(),
        "token_set": bool(token),
        "token_last4": token[-4:] if token else None,
    }


@router.post("/fetch", status_code=202)
async def trigger_fetch() -> dict:
    """Manually trigger a fetch run. Returns run_id for tracking.

    Reads the broker token from the application DB on every call — no cached
    state, no restart needed after the user pastes a new token via
    PUT /api/settings/token.
    """
    if _is_disabled():
        raise HTTPException(
            status_code=503,
            detail={"error": "fetch_disabled", "message": "fetch is disabled by configuration"},
        )

    db_path = _get_sqlite_path()
    use_fake = os.environ.get("ALGOTRADER_INGEST_FAKE") == "1"
    # Only require a broker token when actually going to hit the real Tinkoff
    # API. Fake ingest (ALGOTRADER_INGEST_FAKE=1) doesn't need one — used in
    # dev/test where the SDK isn't installed.
    token_last4: str | None = None
    if not use_fake:
        from ..db.secrets import get_broker_token as _get_token

        token = _get_token(db_path)
        if not token:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "broker_token_missing",
                    "message": "broker token not set — POST /api/settings/token first",
                },
            )
        token_last4 = token[-4:]

    from ..ingestion import pipeline as pipeline_mod

    run_id = pipeline_mod.start_phase(db_path, "discover_universe")

    task = asyncio.create_task(_run_phases(run_id, db_path))
    _running.add(task)
    task.add_done_callback(_running.discard)

    return {
        "run_id": run_id,
        "started_at": _now_iso(),
        "token_last4": token_last4,
    }


async def _run_phases(run_id: int, db_path: str) -> None:
    """Background task: delegate to the canonical backfill runner.

    Pre-`remove-duckdb-and-parquet`, this phase ran both a universe
    discovery AND a legacy `bars.run_bars_phase` that wrote per-ticker
    parquet files. The backfill runner now handles both phases
    (universe + bars in SQLite) so admin fetch is a thin trigger that
    forwards to it.
    """
    from ..config import get_settings
    from ..db.secrets import get_broker_token as _get_token
    from ..ingestion import client as client_mod, pipeline as pipeline_mod

    settings = get_settings()
    use_fake = os.environ.get("ALGOTRADER_INGEST_FAKE") == "1"
    token = _get_token(db_path) if not use_fake else None
    if not use_fake and not token:
        pipeline_mod.end_phase(
            db_path, run_id, status="err", detail="broker_token_missing"
        )
        return

    try:
        client = client_mod.make_client(
            sqlite_path=db_path, use_fake=use_fake, target=None
        )
    except RuntimeError as e:
        logger.error("admin.client.failed", error=str(e))
        pipeline_mod.end_phase(db_path, run_id, status="err", detail=str(e))
        return

    try:
        from ..ingestion.backfill import BackfillRunner

        runner = BackfillRunner(
            client=client,
            db_path=db_path,
            event_sink=_event_sink,
        )
        # Use the runner's history_years config to fetch fresh data
        # into the SQLite `bars` table. The runner already updates
        # `instrument_metadata`, so the operator-facing dashboard stays
        # consistent.
        await runner.run(
            history_years=settings.history_years,
            incremental_threshold_days=2,
        )
        rows = runner.total_bars
        pipeline_mod.end_phase(
            db_path, run_id, status="ok", rows_processed=rows or 0
        )
    except Exception as e:
        logger.error("admin.run.failed", error=str(e))
        pipeline_mod.end_phase(db_path, run_id, status="err", detail=str(e))
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


async def _event_sink(event: object) -> None:
    """No-op sink for admin fetch (real-time progress is served by /api/backfill/events)."""
    return


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat()


def _resolve_target_from_settings(sqlite_path: str) -> str | None:
    """Read BrokerSettings.environment from the app's settings table.

    Returns None when the row is missing or malformed, signalling to
    `make_client()` that it should fall back to its env-var or default
    resolution.
    """
    try:
        import json as _json
        from ..db.sqlite import execute as _exec
        rows = _exec(sqlite_path, "SELECT value FROM settings WHERE key = 'main'", ())
        if not rows:
            return None
        blob = _json.loads(rows[0]["value"])
        broker = blob.get("broker", {}) if isinstance(blob, dict) else {}
        env_name = broker.get("environment")
        if env_name in ("sandbox", "production"):
            return env_name
    except Exception:
        return None
    return None
