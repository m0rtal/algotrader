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


@router.get("/data-pipeline/status")
async def data_pipeline_status() -> dict:
    """Return the last chain-run summary + per-domain freshness.

    Backed by the ``pipeline_log`` table that the worker writes on
    every phase of the daily refresh chain. Per-domain freshness
    comes from the shared ``dividends.freshness.pipeline_freshness_check``
    helper. Safe to poll; no broker calls.
    """
    db_path = _get_sqlite_path()
    import sqlite3

    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pipeline_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "phase TEXT NOT NULL, "
            "started_at TEXT NOT NULL, "
            "finished_at TEXT NOT NULL, "
            "result TEXT NOT NULL, "
            "detail TEXT"
            ")"
        )
        rows = conn.execute(
            "SELECT id, phase, started_at, finished_at, result, detail "
            "FROM pipeline_log ORDER BY id DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        last_run = {"timestamp": None, "rc": None, "phases": []}
    else:
        phases: list[dict] = []
        last_ts: str | None = None
        for r in rows:
            phases.insert(
                0,
                {
                    "phase": r[1],
                    "result": r[4],
                    "detail": r[5] or "",
                    "finished_at": r[3],
                },
            )
            last_ts = r[3]
        any_error = any(p["result"] == "error" for p in phases)
        last_run = {
            "timestamp": last_ts,
            "rc": 1 if any_error else 0,
            "phases": phases,
        }

    freshness: dict = {"bars": {}, "dividends": {}, "corporate_actions": {}}
    try:
        from ..dividends.freshness import pipeline_freshness_check

        freshness = pipeline_freshness_check(db_path, max_chain_age_hours=24)
    except AssertionError as exc:
        freshness = {"stale": True, "reason": str(exc)}
    except Exception:
        freshness = {"bars": {}, "dividends": {}, "corporate_actions": {}}

    return {"last_run": last_run, "freshness": freshness}


# PR #130 (2026-09-24): Multi-level stale indicator for the DataTab.
#
# Why this exists:
#   The operator screen showed only ``pending_counts.stale=4`` — a
#   2-day threshold that hid the real picture. Operationally we
#   care about three different stalenesses, each with its own
#   upstream cause and recovery:
#
#   1. **Bars more than 1 day stale** — yesterday's data hasn't
#      arrived for these figis. Usually a Tinkoff timeout
#      workaround is needed (PR #129) or a fetch needs replaying.
#      ``last_bar_ts == yesterday or today`` is *fresh*;
#      everything older is at least 1-day stale.
#
#   2. **Bars more than 2 days stale** — figis the worker chain
#      hasn't been able to update despite multiple cycles; reflects
#      hard upstream problems (figi no longer listed, source
#      unavailable, network proxy blocked).
#
#   3. **Corporate-actions / dividends pipeline age** — read
#      directly from ``pipeline_log.finished_at`` so the operator
#      sees "last corporate-actions sweep ran 38 hours ago"
#      instead of just a generic ``freshness.stale`` flag.
#
# All three are computed in a single short SQLite transaction per
# request. The endpoint is read-only — no broker calls.


def _stale_breakdown(db_path: str) -> dict:
    """Compute the multi-level stale breakdown for the DataTab.

    Returns a dict with three buckets plus the chain age of each
    non-bars phase (corporate_actions, dividends). The numbers are
    absolute counts of *tradable* figis (share/etf/bond); figis
    that have never had a row in ``bars`` are bucketed into
    ``no_bars_ever`` separately so the UI can call them out.
    """
    from datetime import date, datetime, timedelta

    today = date.today()
    yesterday = today - timedelta(days=1)
    two_days_ago = today - timedelta(days=2)
    import sqlite3

    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        # ``pipeline_log`` is created lazily by the admin endpoint
        # in production, but tests / fresh DBs may not have it yet.
        # Create it here so the breakdown always succeeds and we
        # always return well-formed JSON for the frontend.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pipeline_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "phase TEXT NOT NULL, "
            "started_at TEXT NOT NULL, "
            "finished_at TEXT NOT NULL, "
            "result TEXT NOT NULL, "
            "detail TEXT"
            ")"
        )

        # Tradable figis (share/etf/bond only — futures/options are
        # explicitly excluded from backfill by ingestion).
        tradable = conn.execute(
            "SELECT figi, ticker, class FROM instruments "
            "WHERE figi IS NOT NULL AND class IN ('share', 'etf', 'bond')"
        ).fetchall()

        # Pull the latest bar per tradable figi once. Done in
        # Python loop rather than a per-figi query because the
        # GROUP BY optimisation only matters when N >> 1k, which
        # we already have at 3.8k. SQLite is fine with the loop.
        bars_per_figi: dict[str, str | None] = {}
        for figi, _t, _c in tradable:
            row = conn.execute(
                "SELECT MAX(ts) FROM bars WHERE figi = ?", (figi,)
            ).fetchone()
            bars_per_figi[figi] = row[0] if row else None

        fresh: list[str] = []
        stale_1d: list[tuple[str, str, str]] = []
        stale_2d: list[tuple[str, str, str]] = []
        no_bars: list[tuple[str, str, str]] = []
        for figi, ticker, cls in tradable:
            ts = bars_per_figi.get(figi)
            if ts is None or ts == "":
                no_bars.append((figi, ticker, cls))
                continue
            try:
                d = date.fromisoformat(ts[:10])
            except (TypeError, ValueError):
                no_bars.append((figi, ticker, cls))
                continue
            if d >= yesterday:
                # ``days_since_last_bar in {0, -1}``: today or
                # yesterday. The operator considers these fresh
                # ('<=1 day old'); no action needed.
                fresh.append(figi)
            elif d >= two_days_ago:
                # ``days_since_last_bar == 1``: bars are from the
                # day before yesterday. Operator wants to see this
                # as "stale >1 day" because the target baseline is
                # yesterday.
                stale_1d.append((figi, ticker, cls))
            else:
                # ``days_since_last_bar >= 2``: persistent stale;
                # worker has run at least twice without progress.
                stale_2d.append((figi, ticker, cls))

        # Pipeline ages for non-bars phases. Read the most-recent
        # ``finished_at`` value per phase name from ``pipeline_log``.
        age_hours: dict[str, float | None] = {}
        for phase in ("corporate_actions", "dividends"):
            row = conn.execute(
                "SELECT MAX(finished_at) FROM pipeline_log "
                "WHERE phase = ? AND result = 'ok'",
                (phase,),
            ).fetchone()
            iso = row[0] if row else None
            if iso is None:
                age_hours[phase] = None
                continue
            try:
                finished = datetime.fromisoformat(iso)
                age_hours[phase] = round(
                    (datetime.now() - finished).total_seconds() / 3600, 1
                )
            except (TypeError, ValueError):
                age_hours[phase] = None

        return {
            "as_of": today.isoformat(),
            "yesterday": yesterday.isoformat(),
            "bars": {
                "fresh_or_today": len(fresh),
                "stale_more_than_1_day": len(stale_1d),
                "stale_more_than_2_days": len(stale_2d),
                "no_bars_ever": len(no_bars),
                "tradable_total": len(tradable),
                # Cap the sample to keep the payload small — the
                # DataTab renders at most the first 10 of each class.
                "samples_stale_1d": stale_1d[:10],
                "samples_stale_2d": stale_2d[:10],
                "samples_no_bars": no_bars[:10],
            },
            "pipeline_age_hours": age_hours,
        }
    finally:
        conn.close()


@router.get("/data-stale-breakdown")
async def data_stale_breakdown() -> dict:
    """Multi-level stale breakdown for the DataTab.

    See ``_stale_breakdown`` for the model. Read-only; safe to poll.
    """
    db_path = _get_sqlite_path()
    return _stale_breakdown(db_path)


# ml-data-readiness PR-1 (2026-09-24): ML readiness visibility.
#
# Why this exists:
#   Operators had to grep the database to know how much ML-ready
#   data is on disk. This endpoint surfaces one row of summary
#   numbers so the DataTab can show them inline:
#     - ``rows``: tradeable rows in ``ml_features`` (the long panel)
#     - ``min_ts`` / ``max_ts``: coverage window
#     - ``forward_adjusted_rows``: ``bars_adjusted.adj_close IS NOT NULL``
#       count. Will be 0 until PR-3 ships; the UI shows the value
#       as-is (no special-casing).
#
# The endpoint is read-only; safe to poll (60s interval on the
# frontend side).

@router.get("/ml-readiness")
async def ml_readiness() -> dict:
    """ML-ready row count + last data date. Cheap; safe to poll.

    Used by ``DataTab`` to show a one-line summary so operators do not
    have to grep the database for coverage and lag. ``forward_adjusted_rows``
    will be ``0`` until PR-3 ships ``bars_adjusted`` population; the
    UI shows the value as-is (no special-casing).
    """
    db_path = _get_sqlite_path()
    import sqlite3

    from ..ml.features import row_count, date_range

    rows = row_count(db_path)
    mn, mx = date_range(db_path)
    con = sqlite3.connect(db_path, timeout=5.0)
    try:
        adj = con.execute(
            "SELECT COUNT(*) FROM bars_adjusted "
            "WHERE adj_close IS NOT NULL"
        ).fetchone()[0]
    finally:
        con.close()

    return {
        "rows": rows,
        "min_ts": mn,
        "max_ts": mx,
        "forward_adjusted_rows": adj,
    }


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
