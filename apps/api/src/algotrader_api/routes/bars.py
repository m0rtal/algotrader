"""Bars route — reads from the `bars` SQLite table.

Replaces the previous DuckDB-backed implementation. The endpoint
resolves the symbol (ticker or figi) through the `instruments`
table, then queries the `bars` table directly. No parquet scan, no
DuckDB connection — the request path is O(rows) on an indexed read.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..db.bars_sqlite import list_bars, resolve_figi_for_ticker
from ..db.sqlite import get_connection

router = APIRouter(prefix="/api", tags=["bars"])

# Injected via lifespan
_sqlite_path_holder: dict[str, str] = {}


def set_sqlite_path(path: str) -> None:
    _sqlite_path_holder["path"] = path


def _get_sqlite_path() -> str:
    return _sqlite_path_holder["path"]


def _to_frontend_format(
    ticker: str,
    figi: str,
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convert raw rows to frontend's { symbol, count, first, last, bars: [...] }."""
    if not bars:  # pragma: no cover — defensive: no rows for an empty figi
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
def get_bars(
    symbol: str,
    from_: str | None = Query(default=None, alias="from_"),
    till: str | None = Query(default=None, alias="till"),
) -> dict:
    """Return OHLCV candles for `symbol` (ticker or figi).

    Date filters are inclusive on both ends. Output keys mirror what
    the frontend Drilldown chart already consumes.
    """
    figi = resolve_figi_for_ticker(_get_sqlite_path(), symbol)
    if figi is None:
        raise HTTPException(
            status_code=404, detail={"error": "ticker_not_found", "symbol": symbol}
        )
    bars = _bars_within(figi, from_=from_, till=till)
    return _to_frontend_format(symbol, figi, bars)


def _bars_within(
    figi: str, *, from_: str | None, till: str | None
) -> list[dict[str, Any]]:
    """Read bars for `figi` from the SQLite table, optionally clipped by date."""
    sql = "SELECT ts, open, high, low, close, volume FROM bars WHERE figi = ?"
    params: list[Any] = [figi]
    if from_:
        sql += " AND ts >= ?"
        params.append(from_)
    if till:
        sql += " AND ts <= ?"
        params.append(till)
    sql += " ORDER BY ts"
    conn = get_connection(_get_sqlite_path())
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [
        {
            "ts": r["ts"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
        }
        for r in rows
    ]
