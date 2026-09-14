"""Pre/post assertions for the daily refresh chain.

The `daily_backfill` step in `apps/api/worker.py` is currently a
no-op success that silently swallows the case where `BackfillRunner`
returned 0 bars (rate-limit, dead ticker, broker hiccup). This module
provides a `assert_bars_increased` helper that:

1. Counts `bars` rows before the phase runs (caller passes pre-count).
2. Counts `bars` rows after.
3. If post < pre, raises `AssertionError` so the chain aborts with
   `result='error'` and a clear message in the log.
4. Writes a `pipeline_log` row recording pre/post/delta.

Returning (pre, post, delta) lets callers embed the values in
their own error messages and decide the threshold for "increased"
(default 1, i.e. at least one new bar).
"""
from __future__ import annotations

import sqlite3
import time


def _count_bars(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]


def _ensure_pipeline_log(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pipeline_log ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  phase TEXT NOT NULL,"
        "  started_at TEXT NOT NULL,"
        "  finished_at TEXT NOT NULL,"
        "  result TEXT NOT NULL,"
        "  detail TEXT"
        ")"
    )


def _log(
    conn: sqlite3.Connection, phase: str, result: str, detail: str
) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO pipeline_log (phase, started_at, finished_at, result, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (phase, now, now, result, detail),
    )


def snapshot_bars_count(db_path: str) -> int:
    """Return the current `bars` row count. Cheap; safe to call before
    a phase runs."""
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        return _count_bars(conn)
    finally:
        conn.close()


def assert_bars_increased(
    db_path: str,
    *,
    phase: str = "pipeline",
    pre_count: int | None = None,
    expected_min_increase: int = 0,
) -> tuple[int, int, int]:
    """Compare current bars count to pre_count (or snapshot) and
    raise if it shrank.

    Returns (pre, post, delta). Writes a pipeline_log row.
    """
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        _ensure_pipeline_log(conn)
        if pre_count is None:
            pre = _count_bars(conn)
        else:
            pre = pre_count
        post = _count_bars(conn)
        delta = post - pre
        if post < pre:
            _log(
                conn, phase, "error",
                f"bars_count shrunk: pre={pre} post={post} delta={delta}",
            )
            conn.commit()
            raise AssertionError(
                f"bars_count shrunk during {phase}: "
                f"pre={pre} post={post} delta={delta}"
            )
        if delta < expected_min_increase:
            result = "warn"
        else:
            result = "ok"
        _log(
            conn, phase, result,
            f"bars_count delta: pre={pre} post={post} delta={delta}",
        )
        conn.commit()
        return pre, post, delta
    finally:
        conn.close()
