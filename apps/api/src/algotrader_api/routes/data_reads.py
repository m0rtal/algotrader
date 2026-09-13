"""Read-only data routes that power the dashboard tabs.

Each endpoint returns an honest empty / default value for its schema so
the UI falls into its built-in "n/a" state instead of seeing a 404.
Endpoints with a real backing store (bars in SQLite, settings in
SQLite) map to the underlying query.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..db.sqlite import execute as sqlite_exec
from ..observability.logging import get_logger

logger = get_logger("algotrader_api.data_reads")

router = APIRouter(prefix="/api", tags=["data-reads"])

# Sqlite path injected via app state - set in main.py lifespan.
_sqlite_path_holder: dict[str, str] = {}


def set_sqlite_path(path: str) -> None:
    _sqlite_path_holder["path"] = path


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


@router.get("/tickers")
def get_tickers() -> list:
    """Per-ticker metadata for the Bars tab.

    `fileSize` is preserved (always 0) so the UI header strip keeps
    rendering - there is no on-disk file to size any more.
    """
    overview_rows = sqlite_exec(
        _get_sqlite_path(),
        """
        SELECT
            b.figi                                   AS figi,
            COUNT(*)                                 AS bars,
            MIN(b.ts)                                AS first_ts,
            MAX(b.ts)                                AS last_ts
        FROM bars b
        WHERE b.figi IS NOT NULL
        GROUP BY b.figi
        """,
        (),
    )
    instrument_rows = sqlite_exec(
        _get_sqlite_path(),
        "SELECT ticker, name, sector, currency, lot_size, figi FROM instruments",
        (),
    )
    by_figi = {r["figi"]: r for r in instrument_rows if r["figi"]}
    by_ticker = {r["ticker"]: r for r in instrument_rows if r["ticker"]}

    out: list[dict] = []
    for r in overview_rows:
        meta = by_figi.get(r["figi"]) or by_ticker.get(r["figi"])
        symbol = meta["ticker"] if meta else r["figi"]
        out.append(
            {
                "symbol": symbol,
                "name": meta["name"] if meta else "",
                "sector": meta["sector"] if meta and meta["sector"] else "",
                "price": 0,
                "bars": int(r["bars"]),
                "firstDate": str(r["first_ts"]),
                "lastDate": str(r["last_ts"]),
                "fileSize": 0,
                "gaps": 0,
                "currency": meta["currency"] if meta and meta["currency"] else "",
                "lotSize": int(meta["lot_size"]) if meta and meta["lot_size"] else 0,
            }
        )
    return out
