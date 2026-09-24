"""ML-ready data shape helpers.

Thin convenience functions over the ``ml_features`` view. Kept here
so tests and the admin endpoint share one path; the SQL itself lives
in migration 021.
"""
from __future__ import annotations

import sqlite3
from typing import Optional


def row_count(db_path: str) -> int:
    """Number of tradeable rows in ``ml_features``.

    Trades freshness for accuracy: WAL mode means rows that have been
    written but not yet checkpointed by the writer are visible to this
    reader (SQLite reads WAL before main file), so the count reflects
    in-flight writes.
    """
    con = sqlite3.connect(db_path, timeout=5)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM ml_features WHERE is_tradeable=1"
        ).fetchone()[0]
    finally:
        con.close()


def date_range(db_path: str) -> tuple[Optional[str], Optional[str]]:
    """Inclusive ``(min_ts, max_ts)`` of tradeable rows, or ``(None, None)``
    when there are no rows yet (cold DB before first chain run).
    """
    con = sqlite3.connect(db_path, timeout=5)
    try:
        r = con.execute(
            "SELECT MIN(ts), MAX(ts) FROM ml_features WHERE is_tradeable=1"
        ).fetchone()
        return r[0], r[1]
    finally:
        con.close()
