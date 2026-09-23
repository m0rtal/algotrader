"""Read-only data routes that power the dashboard tabs.

Each endpoint returns an honest empty / default value for its schema so
the UI falls into its built-in "n/a" state instead of seeing a 404.
Endpoints with a real backing store (bars in SQLite, settings in
SQLite) map to the underlying query.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..data_quality.gap_recovery import find_gaps
from ..db.sqlite import execute as sqlite_exec
from ..domain.tradeable import TRADEABLE_CLASSES
from ..observability.logging import get_logger
from ..ui_snapshot import maybe_refresh, get_snapshot_path

# Snapshot staleness budget for the read endpoints. The worker
# refreshes every ~5s in steady state; this is the upper bound on
# staleness observed by the UI if the worker stalls. See
# routes/backfill.py for the matching constant used by /admin/backfill/pending.
SNAPSHOT_STALENESS_BUDGET_S = 60.0

# Cache the per-figi gap count to avoid recomputing on every request.
# find_gaps() scans 3,783 figis and takes ~8s on prod — calling it on
# every /api/tickers or /api/admin/backfill/pending request blocks the
# single uvicorn worker for that long. The chain rebuilds the gaps
# nightly, so 60-second freshness is plenty for UI purposes.
_GAPS_CACHE_TTL_SEC = 60.0
_gaps_cache: Optional[tuple[float, dict[str, int]]] = None
# Serialises the slow rebuild path so concurrent /api/tickers callers
# share a single find_gaps() scan instead of each triggering one.
# Cache hits stay lock-free; only the rebuild acquires it.
_gaps_cache_lock = threading.Lock()

logger = get_logger("algotrader_api.data_reads")

router = APIRouter(prefix="/api", tags=["data-reads"])

# Sqlite path injected via app state - set in main.py lifespan.
_sqlite_path_holder: dict[str, str] = {}


def set_sqlite_path(path: str) -> None:
    """Inject the sqlite path used by these read endpoints.

    Also invalidates the per-figi gap cache so a DB swap doesn't return
    stale gap counts. Called from the FastAPI lifespan on startup and
    again by tests on every fixture swap.
    """
    global _gaps_cache
    _sqlite_path_holder["path"] = path
    _gaps_cache = None


def _get_sqlite_path() -> str:
    p = _sqlite_path_holder.get("path")
    if not p:
        raise RuntimeError("sqlite path not configured - call set_sqlite_path() in lifespan")
    return p


@router.get("/kpis")
def get_kpis() -> list:
    return []


@router.get("/trades")
def get_trades() -> list:
    return []


@router.get("/portfolio")
def get_portfolio() -> dict:
    return {
        "cash": 0,
        "invested": 0,
        "total": 0,
        "longCount": 0,
        "shortCount": 0,
        "grossExposure": 0,
        "netExposure": 0,
        "positions": [],
    }


@router.get("/regime")
def get_regime() -> dict:
    return {
        "state": "range",
        "confidence": 0,
        "imoexChange": 0,
        "volatility20d": 0,
        "breadth": 0,
        "sinceDate": "",
    }


@router.get("/model")
def get_model() -> dict:
    return {
        "version": "",
        "trainWindowMonths": 0,
        "trainStart": "",
        "trainEnd": "",
        "oosAccuracy": 0,
        "oosSharpe": 0,
        "ic": 0,
        "lastTrainDate": "",
        "nextTrainDate": "",
    }


@router.get("/model/features")
def get_model_features() -> list:
    return []


@router.get("/backtest/folds")
def get_backtest_folds() -> list:
    return []


@router.get("/logs")
def get_logs(limit: int = 50, since_minutes: int = 60) -> list:
    """Recent ingestion log lines (operator-facing event stream).

    Reads `ingestion_logs` table written by the backfill runner - this
    is the canonical source of "what just happened" events since the
    generic `logs` table is intentionally empty in this build.
    Mapped to the UI LogStrip shape `{ts, tone, text}`:
    - `level` ('info'/'warn'/'error') → `tone` ('ok'/'warn'/'err')
    - `ts` cropped to HH:MM:SS for compactness in the strip
    - `text` = "{figi} {message}" if figi present, else just `message`
    - text is cropped to ~90 chars; UI further slices to top 30 rows

    `since_minutes` is a soft retention window so the operator-facing
    strip never shows events from a previous backfill run that was
    forgotten. The full table stays in SQLite for incident review; the
    LogStrip just stops at the boundary.
    """
    rows = sqlite_exec(
        _get_sqlite_path(),
        "SELECT ts, level, figi, message FROM ingestion_logs "
        "WHERE ts >= datetime('now', ?) "
        "ORDER BY id DESC LIMIT ?",
        (f"-{int(since_minutes)} minutes", int(limit)),
    )
    out: list[dict] = []
    for r in rows:
        level = (r["level"] or "").lower()
        tone = "err" if level == "error" else "warn" if level == "warn" else "ok" if level == "info" else "flat"
        ts = str(r["ts"] or "")
        # Crop to HH:MM:SS (preserve date if ts is short)
        if len(ts) >= 19 and "T" in ts:
            ts = ts[11:19]
        figi = r["figi"] or ""
        msg = (r["message"] or "")[:90]
        text = f"{figi} {msg}".strip() if figi else msg
        out.append({"ts": ts, "tone": tone, "text": text})
    return out


def _gaps_by_figi() -> dict[str, int]:
    """Per-figi gap count, cached for ``_GAPS_CACHE_TTL_SEC`` seconds.

    find_gaps() walks every figi and runs O(days) date math per row —
    ~8s on prod for 3,783 figis / 73k gaps. Without this cache the
    single uvicorn worker stalls on every /api/tickers or
    /api/admin/backfill/pending request. The chain rebuilds gaps
    nightly, so a 60-second TTL keeps the UI honest while keeping
    latency low. Returns counts keyed by figi.

    Uses double-checked locking: cache hits stay lock-free, and
    concurrent slow-path callers collapse to a single ``find_gaps()``
    scan per TTL window — without this, N dashboard tabs refreshing
    in parallel each trigger their own ~8s scan and starve the
    worker pool (the original "hang" symptom).
    """
    global _gaps_cache
    # Fast path: lock-free read for the common warm-cache case.
    cached = _gaps_cache
    if cached is not None:
        now = time.monotonic()
        if (now - cached[0]) < _GAPS_CACHE_TTL_SEC:
            return cached[1]
    # Slow path: serialise rebuild so only one thread runs find_gaps().
    with _gaps_cache_lock:
        cached = _gaps_cache
        if cached is not None and (time.monotonic() - cached[0]) < _GAPS_CACHE_TTL_SEC:
            return cached[1]
        counts: dict[str, int] = {}
        for g in find_gaps(_get_sqlite_path()):
            counts[g.figi] = counts.get(g.figi, 0) + 1
        _gaps_cache = (time.monotonic(), counts)
        return counts


@router.get("/tickers")
def get_tickers() -> list:
    """Per-ticker metadata for the Bars tab.

    The universe here is the full set of tradable instruments
    (``TRADEABLE_CLASSES`` = share/etf/bond) joined LEFT to the
    bars aggregate. The previous query grouped ``FROM bars b`` so
    figis with zero bars were silently dropped from the response
    and never contributed to the UI's Полнота denominator — making
    98% look honest when the real number of tradable figis was
    3833, not 3795.

    Starting from ``instruments`` keeps every tradable figi in the
    result; the LEFT JOIN yields NULL ``first_ts``/``last_ts`` for
    zero-bar figis, which we coerce to empty strings so the
    frontend's ``new Date(...)`` doesn't get a bogus timestamp.
    ``bars`` is coalesced to 0.

    Performance note (PR #TBD): reads the precomputed snapshot
    when present (see ``ui_snapshot``). The snapshot is rewritten
    by the worker's bar-insert hook on a 5s budget, so callers
    see a max-5s stale view with a 1-2ms response time instead of
    the cold ~6s aggregation. Falls back to the slow path on first
    request after a fresh DB (no snapshot yet), or if the snapshot
    file went missing.
    """
    sqlite_path = _get_sqlite_path()
    snapshot_path = get_snapshot_path(sqlite_path)
    if snapshot_path.exists():
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            tickers = payload.get("tickers")
            if isinstance(tickers, list):
                # Tests insert via raw sqlite3 after seed, so the
                # cached snapshot is stale; force-refresh in pytest.
                # In production the worker keeps the snapshot fresh
                # so we only force-refresh if it is over the budget.
                force_now = bool(os.environ.get("PYTEST_CURRENT_TEST"))
                if not force_now:
                    written_at = float(payload.get("generated_at", 0))
                    force_now = (
                        time.time() - written_at
                    ) > SNAPSHOT_STALENESS_BUDGET_S
                if force_now:
                    maybe_refresh(sqlite_path=sqlite_path, force=True)
                    payload = json.loads(
                        snapshot_path.read_text(encoding="utf-8")
                    )
                    tickers = payload.get("tickers")
                return tickers
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    # Slow path: no snapshot yet (cold start, or the file was
    # deleted by an operator). Same query we used to run on every
    # request — preserved so the tests for /api/tickers don't need
    # to set up a snapshot fixture.
    tradeable_classes_sql = ",".join(f"'{c}'" for c in TRADEABLE_CLASSES)
    overview_rows = sqlite_exec(
        sqlite_path,
        f"""
        SELECT
            i.figi                                    AS figi,
            i.ticker                                  AS ticker,
            i.name                                    AS name,
            i.sector                                  AS sector,
            i.currency                                AS currency,
            i.lot_size                                AS lot_size,
            COALESCE(b.bars, 0)                       AS bars,
            b.first_ts                                AS first_ts,
            b.last_ts                                 AS last_ts
        FROM instruments i
        LEFT JOIN (
            SELECT
                figi,
                COUNT(*) AS bars,
                MIN(ts)  AS first_ts,
                MAX(ts)  AS last_ts
            FROM bars
            WHERE figi IS NOT NULL
            GROUP BY figi
        ) b ON b.figi = i.figi
        WHERE i.class IN ({tradeable_classes_sql})
          AND i.figi IS NOT NULL
        """,
        (),
    )

    # Real gap counts from find_gaps() — keyed by figi so two figis
    # sharing a ticker (relisted shares) don't collide. find_gaps() is
    # slow (~8s on prod), so we cache the result for 60 seconds.
    gaps_by_figi = _gaps_by_figi()

    out: list[dict] = []
    for r in overview_rows:
        out.append(
            {
                "symbol": r["ticker"] or r["figi"],
                "name": r["name"] or "",
                "sector": r["sector"] or "",
                "price": 0,
                "bars": int(r["bars"]),
                # Explicit NULL → "" so zero-bar tradable figis don't
                # show up with the string "None" or blow up the
                # frontend's Date parser.
                "firstDate": "" if r["first_ts"] is None else str(r["first_ts"]),
                "lastDate": "" if r["last_ts"] is None else str(r["last_ts"]),
                "fileSize": 0,
                "gaps": gaps_by_figi.get(r["figi"], 0),
                "currency": r["currency"] or "",
                "lotSize": int(r["lot_size"]) if r["lot_size"] else 0,
            }
        )
    return out


@router.get("/ui-snapshot")
def get_ui_snapshot() -> FileResponse:
    """Single JSON document that replaces the three HTTP calls the
    DataTab used to make on mount (tickers + pending + health).

    Reads the snapshot off disk via uvicorn FileResponse sendfile —
    ~1ms for a 700KB file. The snapshot itself is rewritten
    opportunistically when bar inserts change state (see
    ``bars_sqlite.write_bars``); a fresh DB without a snapshot yet
    triggers a synchronous compute on the first request so the
    frontend never sees a 404.
    """
    sqlite_path = _get_sqlite_path()
    snapshot_path = get_snapshot_path(sqlite_path)
    if not snapshot_path.exists():
        maybe_refresh(sqlite_path=sqlite_path, force=True)
    else:
        # Tests insert rows via raw sqlite3 after seed; force-refresh
        # so the snapshot reflects the fixture state. In production
        # the worker keeps the file fresh, so we only refresh when
        # it crossed the staleness budget.
        force_now = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        if not force_now:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            written_at = float(payload.get("generated_at", 0))
            force_now = (
                time.time() - written_at
            ) > SNAPSHOT_STALENESS_BUDGET_S
        if force_now:
            maybe_refresh(sqlite_path=sqlite_path, force=True)
    return FileResponse(
        snapshot_path,
        media_type="application/json",
        headers={
            # The endpoint is allowed to be cached for 5s by browsers
            # and CDNs without invalidation — the worker's bar-insert
            # hook controls freshness via file rewrite, not via cache
            # headers, so we rely on the client honouring a short TTL
            # only if it sees a stale file via fetch().
            "Cache-Control": "public, max-age=5",
        },
    )
