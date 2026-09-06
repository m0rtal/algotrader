"""Health endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Response

from ..db import duck, sqlite
from ..observability.logging import get_logger

router = APIRouter()
logger = get_logger("algotrader_api.health")

# path injected via lifespan
_bars_dir_holder: dict[str, str] = {}


def set_bars_dir(path: str) -> None:
    _bars_dir_holder["path"] = path


def _get_bars_dir() -> str:
    p = _bars_dir_holder["path"]
    return p


@router.get("/health")
def health(response: Response) -> dict:
    """Service health check.

    Returns 200 if both SQLite and DuckDB are reachable with bars_count >= 0.
    Returns 503 if either backend is unreachable.
    """
    sqlite_ok = "ok"
    duck_ok = "ok"
    bars_count = 0
    status = "ok"

    try:
        sqlite.execute(_sqlite_path(), "SELECT 1", ())
    except Exception as e:
        sqlite_ok = f"error: {e}"
        status = "degraded"

    try:
        bars_count = duck.count_bars(_get_bars_dir())
    except Exception as e:
        duck_ok = f"error: {e}"
        status = "degraded"

    body = {
        "status": status,
        "sqlite": sqlite_ok,
        "duckdb": duck_ok,
        "bars_count": bars_count,
    }
    if status != "ok":
        response.status_code = 503
    return body


_sqlite_path_holder: dict[str, str] = {}


def set_sqlite_path(path: str) -> None:
    _sqlite_path_holder["path"] = path


def _sqlite_path() -> str:
    return _sqlite_path_holder["path"]
