"""Bars route."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..db import duck

router = APIRouter(prefix="/api", tags=["bars"])

# Injected via lifespan
_bars_dir_holder: dict[str, str] = {}


def set_bars_dir(path: str) -> None:
    _bars_dir_holder["path"] = path


def _get_bars_dir() -> str:
    return _bars_dir_holder["path"]


def _to_frontend_format(ticker: str, bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert raw rows to frontend's { symbol, count, first, last, bars: [{t,o,h,l,c,v}, ...] }."""
    if not bars:
        return {"symbol": ticker, "count": 0, "first": "", "last": "", "bars": []}
    return {
        "symbol": ticker,
        "count": len(bars),
        "first": str(bars[0]["ts"]),
        "last": str(bars[-1]["ts"]),
        "bars": [
            {
                "t": str(b["ts"]),
                "o": float(b["open"]),
                "h": float(b["high"]),
                "l": float(b["low"]),
                "c": float(b["close"]),
                "v": int(b["volume"]),
            }
            for b in bars
        ],
    }


@router.get("/bars/{symbol}")
def get_bars(symbol: str, from_: str | None = None, till: str | None = None) -> dict:
    available = duck.list_tickers(_get_bars_dir())
    if symbol not in available:
        raise HTTPException(status_code=404, detail={"error": "ticker_not_found", "symbol": symbol})
    rows = duck.query_bars(_get_bars_dir(), symbol, date_from=from_, date_till=till)
    return _to_frontend_format(symbol, rows)
