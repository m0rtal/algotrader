"""Read-only data routes that power the dashboard tabs.

Each endpoint returns an honest empty / default value for its schema so
the UI falls into its built-in "n/a" state instead of seeing a 404.
Endpoints with a real backing store (bars in DuckDB, settings in SQLite,
tickers derived from DuckDB) map to the underlying query.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..db import duck
from ..observability.logging import get_logger

logger = get_logger("algotrader_api.data_reads")

router = APIRouter(prefix="/api", tags=["data-reads"])

# Bars dir injected via app state — set in main.py lifespan.
_bars_dir_holder: dict[str, str] = {}


def set_bars_dir(path: str) -> None:
    _bars_dir_holder["path"] = path


def _get_bars_dir() -> str:
    p = _bars_dir_holder.get("path")
    if not p:
        raise RuntimeError("bars dir not configured — call set_bars_dir() in lifespan")
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
def get_logs() -> list:
    return []


@router.get("/tickers")
def get_tickers() -> list:
    """Per-ticker metadata for the Bars tab.

    Reads from DuckDB (bars view across all backfilled parquet files).
    Returns one row per ticker with `symbol`, `bars` count, first/last
    dates. `name`, `sector`, `price`, `fileSize`, `gaps` are placeholder
    0 / '' because we do not yet join instrument metadata (it's an open
    follow-up: a name map / sector classifier would belong here once the
    portfolio team needs them). Returning the real shape keeps the UI
    populated so the operator can see "what is in DuckDB right now".
    """
    bars_dir = _get_bars_dir()
    return [
        {
            "symbol": r["ticker"],
            "name": "",
            "sector": "",
            "price": 0,
            "bars": int(r["bars"]),
            "firstDate": str(r["first_ts"]),
            "lastDate": str(r["last_ts"]),
            "fileSize": 0,
            "gaps": 0,
        }
        for r in duck.query_ticker_overview(bars_dir)
        if r["ticker"] is not None
    ]
