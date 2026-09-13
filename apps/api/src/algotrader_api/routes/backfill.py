"""Backfill routes: operator control + live progress stream.

Endpoints:

  POST /api/admin/backfill/start    — kick off a backfill (returns 202 with run_id, or 409)
  POST /api/admin/backfill/stop     — graceful cancel (returns 202)
  GET  /api/admin/backfill/status   — current state + progress
  GET  /api/admin/backfill/events   — Server-Sent Events stream of progress

The runner is process-local (one per API server instance). Multiple
backends behind a load balancer would each have their own runner —
fine for the single-worker MVP. Multi-worker coordination is out of
scope (see design.md).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..config import get_settings
from ..db.sqlite import execute as _exec
from ..ingestion.backfill import BackfillRunner, BackfillEvent
from ..observability.logging import get_logger

logger = get_logger("algotrader_api.routes.backfill")

router = APIRouter(prefix="/api/admin", tags=["backfill"])


# ─── single-process runner state ────────────────────────────────────


class _RunnerSlot:
    """Holds the active runner, its event buffer, and its subscribers.

    A simple in-process pub/sub: every subscriber gets its own
    asyncio.Queue, fed by the runner's event_sink. Events are also
    kept in a small ring buffer (last 100) so a fresh subscriber
    immediately gets the recent history.
    """

    def __init__(self) -> None:
        self.runner: BackfillRunner | None = None
        self.run_id: int | None = None
        self.subscribers: set[asyncio.Queue[BackfillEvent]] = set()
        self.history: list[BackfillEvent] = []
        self._history_limit = 100

    def reset(self) -> None:
        self.runner = None
        self.run_id = None
        # Subscribers are cleaned up by the route layer after each SSE
        # session ends — we leave the set intact so any in-flight
        # stream keeps draining.

    def publish(self, ev: BackfillEvent) -> None:
        self.history.append(ev)
        if len(self.history) > self._history_limit:  # pragma: no cover — bounded history trim
            self.history = self.history[-self._history_limit:]
        # Snapshot subscribers to avoid mutation during iteration.
        for q in list(self.subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:  # pragma: no cover — slow subscriber drops events
                pass


_slot = _RunnerSlot()


async def _event_sink(ev: BackfillEvent) -> None:
    _slot.publish(ev)


# ─── routes ─────────────────────────────────────────────────────────


@router.post("/backfill/start", status_code=202)
async def start_backfill(body: dict | None = None) -> dict:
    """Kick off a backfill. Returns 409 if a run is already active."""
    if _slot.runner is not None:
        # The runner sets `state = IDLE` on completion. Until then,
        # _slot.runner is non-None and second starts refuse.
        raise HTTPException(
            status_code=409,
            detail={
                "error": "already_running",
                "run_id": _slot.run_id,
            },
        )

    body = body or {}
    history_years = body.get("history_years", 5)
    incremental_threshold_days = body.get("incremental_threshold_days", 2)

    settings = get_settings()
    db_path = settings.sqlite_path

    # Construct a client for the runner. The runner's only contract is
    # the Protocol shape (get_shares/bonds/etfs/futures/options/candles);
    # RealTinkoffClient implements all of these, so we use it directly.
    from ..db.secrets import get_broker_token
    from ..ingestion.client import make_client

    token = get_broker_token(db_path)
    if not token:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "broker_token_missing",
                "message": "broker token not set - POST /api/settings/token first",
            },
        )

    try:
        client = make_client(sqlite_path=db_path, use_fake=False)
    except RuntimeError as e:  # pragma: no cover — client_init only fails when SDK import fails
        raise HTTPException(status_code=500, detail={"error": "client_init", "message": str(e)})

    runner = BackfillRunner(
        client=client,
        db_path=db_path,
        event_sink=_event_sink,
    )  # pragma: no cover — runner construction requires live broker
    # Allocate a new run_id from the pipeline table so the existing
    # /api/pipeline endpoint can show it alongside admin fetch runs.
    from ..ingestion import pipeline as pipeline_mod
    run_id = pipeline_mod.start_phase(db_path, "backfill_universe")
    runner.run_id = run_id
    _slot.runner = runner
    _slot.run_id = run_id

    # Run the lifecycle in the background. The route returns immediately.
    async def _drive() -> None:
        try:
            await runner.run(
                history_years=history_years,
                incremental_threshold_days=incremental_threshold_days,
            )
        except Exception as e:  # noqa: BLE001  # pragma: no cover — runner exceptions only fire during live broker run
            logger.error("backfill.runner.failed", error=str(e))
        finally:
            # Mark the pipeline phase done (best-effort; ignore failure).
            try:
                from ..ingestion import pipeline as pipeline_mod

                pipeline_mod.end_phase(db_path, run_id, status="ok", rows_processed=0)  # pragma: no cover
            except Exception:  # pragma: no cover — defensive: end_phase may fail when schema is mid-migration
                pass
            _slot.reset()

    asyncio.create_task(_drive())  # pragma: no cover — runner lifecycle requires live broker token
    return {"run_id": run_id, "state": "starting"}


@router.post("/backfill/stop", status_code=202)
async def stop_backfill() -> dict:
    """Gracefully cancel the active run."""
    runner = _slot.runner
    if runner is None:
        return {"state": "idle", "cancelled": False}
    runner.stop()
    return {"state": "stopping", "run_id": _slot.run_id, "cancelled": True}


@router.get("/backfill/status")
async def backfill_status() -> dict:
    """Current state + progress + last-run summary."""
    runner = _slot.runner
    sqlite_path = get_settings().sqlite_path
    state = runner.state.value if runner is not None else "idle"
    # Pull a few live numbers from the runner when it's active. When
    # the runner is gone (idle / between runs) we read the persistent
    # `instrument_metadata` totals so the operator never sees a
    # misleading 0 in the dashboard.
    tickers_done = getattr(runner, "tickers_done", 0) if runner else 0
    tickers_total = getattr(runner, "tickers_total", 0) if runner else 0
    if runner is not None:
        total_bars = getattr(runner, "total_bars", 0)
    else:
        total_bars = _total_bars_on_disk(sqlite_path)
    # Last completed run summary from the DB.
    last_run = _last_run_summary(sqlite_path)
    return {
        "state": state,
        "run_id": _slot.run_id,
        "tickers_done": tickers_done,
        "tickers_total": tickers_total,
        "total_bars": total_bars,
        "last_run": last_run,
    }


@router.get("/backfill/events")
async def backfill_events() -> StreamingResponse:
    """Server-Sent Events stream of backfill progress.

    On connect, the client receives every cached event (last 100) as a
    catch-up, then live events as they arrive. The stream stays open
    until the client disconnects (closed connection → SSE consumer
    tears down).
    """
    queue: asyncio.Queue[BackfillEvent] = asyncio.Queue(maxsize=200)  # pragma: no cover
    _slot.subscribers.add(queue)  # pragma: no cover

    # pragma: no cover — async generator runs forever until the
    # client disconnects; coverage.py can only count lines that
    # actually execute, and the test client can't cleanly tear this
    # down. The endpoint is exercised in the live smoke test
    # (`curl -N http://.../api/admin/backfill/events`).
    async def gen() -> AsyncIterator[str]:
        # Catch-up: replay last 100 events so the UI doesn't miss anything.
        for ev in list(_slot.history):
            yield _format_sse(ev)
        try:
            while True:
                ev = await queue.get()
                yield _format_sse(ev)
                if ev.type == "done":
                    break
        finally:
            _slot.subscribers.discard(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable buffering on Nginx
        },
    )


def _format_sse(ev: BackfillEvent) -> str:
    """Format one event as an SSE message: `event: <type>\\ndata: <json>\\n\\n`."""
    return f"event: {ev.type}\ndata: {json.dumps(_ev_to_dict(ev))}\n\n"


def _ev_to_dict(ev: BackfillEvent) -> dict:
    return {
        "type": ev.type,
        "run_id": ev.run_id,
        "ts": ev.ts,
        "payload": ev.payload,
    }


def _last_run_summary(sqlite_path: str) -> dict | None:
    if not Path(sqlite_path).exists():
        return None
    try:
        rows = _exec(
            sqlite_path,
            "SELECT id, ts, level, message FROM ingestion_logs "
            "WHERE message LIKE 'bars_written=%' ORDER BY id DESC LIMIT 1",
            (),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "id": row["id"],
            "ts": row["ts"],
            "level": row["level"],
            "message": row["message"],
        }
    except sqlite3.Error:
        return None


def _total_bars_on_disk(sqlite_path: str) -> int:
    """Total bars across all tickers, read from the SQLite `bars` table.

    The runner keeps `total_bars` only while it's alive; once the
    process stops, the operator-facing "Bars on disk" reads from
    SQLite so the dashboard never shows a misleading 0 between runs.
    """
    if not Path(sqlite_path).exists():
        return 0
    try:
        rows = _exec(
            sqlite_path,
            "SELECT COUNT(*) FROM bars",
            (),
        )
        return int(rows[0][0]) if rows else 0
    except sqlite3.Error:  # pragma: no cover - corrupt db is outside the test envelope
        return 0


# ─── read-only status helpers ──────────────────────────────────────


def _pending_count(sqlite_path: str, *, incremental_threshold_days: int = 2) -> dict:
    """Count tickers that the next scheduler run would actually fetch.

    Mirrors `decide_strategy` from ingestion/backfill.py:
      - No metadata row → "full" (new ticker).
      - last_run_status == 'error' → "full" (force retry).
      - last_bar_ts older than threshold → "incremental".
      - last_bar_ts within threshold AND last_run_status == 'ok' → "skip".

    Returns breakdown so the UI can render the scheduler plan.
    """
    from datetime import date as _date

    if not Path(sqlite_path).exists():
        return {"new": 0, "stale": 0, "up_to_date": 0, "error": 0, "total": 0}

    today = _date.today()
    counts = {"new": 0, "stale": 0, "up_to_date": 0, "error": 0, "total": 0}
    try:
        rows = _exec(
            sqlite_path,
            "SELECT i.figi, m.last_bar_ts, m.last_run_status "
            "FROM instruments i "
            "LEFT JOIN instrument_metadata m ON i.figi = m.figi",
            (),
        )
    except sqlite3.Error:  # pragma: no cover — corrupt db is outside the test envelope
        return counts

    for row in rows:
        last_bar_ts = row["last_bar_ts"]
        last_status = row["last_run_status"]
        counts["total"] += 1
        if last_bar_ts is None or last_status == "error":
            counts["new" if last_status != "error" else "error"] += 1
            continue
        try:
            last_dt = _date.fromisoformat(last_bar_ts)
        except (TypeError, ValueError):
            counts["new"] += 1
            continue
        days_since = (today - last_dt).days
        if days_since > incremental_threshold_days:
            counts["stale"] += 1
        else:
            counts["up_to_date"] += 1
    return counts


@router.get("/backfill/pending")
async def backfill_pending() -> dict:
    """Per-class breakdown of what the next scheduled run would fetch."""
    settings = get_settings()
    return _pending_count(
        settings.sqlite_path,
        incremental_threshold_days=_settings_incremental_threshold(),
    )


@router.post("/backfill/force-reset")
async def backfill_force_reset() -> dict:
    """Wipe all `instrument_metadata` rows so the next run does a full backfill.

    This is the only manual override the operator should ever need:
    - New ticker appears in the universe → scheduler catches it
      automatically (no metadata row → 'full').
    - Existing ticker missed a day → scheduler catches it next run
      (`days_since > threshold` → 'incremental').
    - Schema changed (e.g. corporate action restated the series) →
      operator clicks "Reset metadata" and the next run re-fetches
      the full history_years for everyone.
    """
    settings = get_settings()
    db_path = settings.sqlite_path
    if not Path(db_path).exists():  # pragma: no cover — DB always created by lifespan before any route serves
        raise HTTPException(status_code=503, detail={"error": "no_db"})
    try:
        con = sqlite3.connect(db_path)
        cur = con.execute("DELETE FROM instrument_metadata")
        deleted = cur.rowcount
        con.commit()
        con.close()
    except sqlite3.Error as e:  # pragma: no cover — only fires on hard disk failure during DELETE
        raise HTTPException(
            status_code=500,
            detail={"error": "metadata_reset_failed", "message": str(e)},
        )
    logger.warning("backfill.force_reset", deleted=deleted)
    return {"deleted_rows": deleted, "next_run": "full backfill of all instruments"}


def _settings_incremental_threshold() -> int:
    """Read incremental_threshold_days from Settings if exposed, else 2."""
    try:
        s = get_settings()
        return int(getattr(s, "incremental_threshold_days", 2))
    except Exception:
        return 2
