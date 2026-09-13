"""Health endpoint.

Returns 200 if SQLite responds. Returns 503 if unreachable. The
`bars_count` is the total number of rows in the SQLite `bars` table
- no DuckDB or filesystem scan is involved.
"""
from __future__ import annotations

from fastapi import APIRouter, Response

from ..db import sqlite
from ..db.bars_sqlite import count_bars as _bars_count
from ..observability.logging import get_logger

router = APIRouter()
logger = get_logger("algotrader_api.health")

# Sqlite path injected via lifespan.
_sqlite_path_holder: dict[str, str] = {}


def set_sqlite_path(path: str) -> None:
    _sqlite_path_holder["path"] = path


def _sqlite_path() -> str:
    return _sqlite_path_holder["path"]


@router.get("/health")
def health(response: Response) -> dict:
    """Service health check.

    Returns 200 with `bars_count` from the SQLite `bars` table if
    both `SELECT 1` and the bars count query succeed. Returns 503 if
    either fails.
    """
    sqlite_ok = "ok"
    bars_ok = "ok"
    bars_count = 0
    status = "ok"

    try:
        sqlite.execute(_sqlite_path(), "SELECT 1", ())
    except Exception as e:
        sqlite_ok = f"error: {e}"
        status = "degraded"

    try:
        bars_count = _bars_count(_sqlite_path())
    except Exception as e:
        bars_ok = f"error: {e}"
        status = "degraded"

    body = {
        "status": status,
        "sqlite": sqlite_ok,
        "bars": bars_ok,
        "bars_count": bars_count,
    }
    if status != "ok":
        response.status_code = 503
    return body
