"""SQLite helpers for the `bars` table.

Write path
----------
`replace_bars_for_figi(sqlite_path, figi, candles)` is called by
`BackfillRunner._backfill_one` after every successful fetch. It
performs a single SQLite transaction that wipes the existing rows
for `figi` and inserts the new candles. The same transaction also
updates `instrument_metadata.total_bars`, `first_bar_ts`, and
`last_bar_ts` so the pending counter and Bars tab header stay in
sync with the actual row count.

Read path
---------
`resolve_figi_for_ticker(sqlite_path, symbol)` is called by
`/api/bars/<symbol>` to translate a ticker (or figi) into the
canonical figi used by the `bars` table.
"""
from __future__ import annotations

from datetime import date as _date
from typing import Iterable

from .sqlite import get_connection


def _row(c: dict | object) -> tuple:
    """Normalize a candle into a tuple suitable for INSERT.

    Tinkoff's SDK returns candle dataclass-like objects
    (`c.open`, `c.high`, `c.ts.year`) when the raw gRPC client is used,
    and plain dicts (`c["open"]`, `c["high"]`, `c["ts"]`) when the
    SDK's `MarketDataService.get_candles()` is used. The bars-SQLite
    mirror accepts both shapes.
    """
    def _get(key: str, default=None):
        if isinstance(c, dict):
            return c.get(key, default)
        return getattr(c, key, default)

    ts = _get("ts")
    if not isinstance(ts, _date):
        # dict with ts string OR dataclass with ts.time.{year,month,day}
        if isinstance(c, dict):
            t = c.get("time") or c.get("time_") or {}
            if isinstance(t, dict):
                y, m, d = t.get("year"), t.get("month"), t.get("day")
            else:  # pragma: no cover — defensive: future SDK may use a different attribute
                y, m, d = getattr(t, "year", None), getattr(t, "month", None), getattr(t, "day", None)
        else:
            t = getattr(c, "time", None)
            y, m, d = getattr(t, "year", None), getattr(t, "month", None), getattr(t, "day", None)
        if y and m and d:
            ts = _date(y, m, d)
        elif isinstance(ts, str):
            ts = ts[:10]
        else:
            ts = None
    if not isinstance(ts, _date):
        ts_str = str(ts)[:10] if ts else None
    else:
        ts_str = ts.isoformat()
    if not ts_str:
        raise ValueError(f"cannot extract ts from candle: {c!r}")

    open_v = _get("open")
    high_v = _get("high")
    low_v = _get("low")
    close_v = _get("close")
    # Handle Quotation dataclass: `.units + .nano / 1e9`. Plain dicts
    # (raw gRPC serialised form) carry nested `{units, nano}` too.
    def _q(v):
        if v is None:
            return None
        if isinstance(v, dict):
            units = v.get("units", 0)
            nano = v.get("nano", 0)
            return float(units) + float(nano or 0) / 1e9
        units = getattr(v, "units", None)
        nano = getattr(v, "nano", 0)
        if units is not None:
            return float(units) + float(nano or 0) / 1e9
        return float(v)  # pragma: no cover — defensive: raw numeric value

    return (
        ts_str,
        _q(open_v),
        _q(high_v),
        _q(low_v),
        _q(close_v),
        int(_get("volume") or 0),
    )


def replace_bars_for_figi(
    sqlite_path: str,
    figi: str,
    candles: Iterable[dict],
) -> int:
    """Insert or replace all candles for `figi`.

    Each candle may be a dict (SDK's `MarketDataService` output) or a
    dataclass-like object (raw gRPC response). Returns the number of
    rows written. Empty list is a no-op.

    The function runs everything in a single transaction so a
    concurrent reader via SQLite WAL sees either the pre-call or
    post-call snapshot, never a half-written state.
    """
    rows = []
    for c in candles:
        ts_str, o, h, l, cl, v = _row(c)
        rows.append((figi, ts_str, float(o), float(h), float(l), float(cl), int(v)))

    if not rows:
        return 0

    conn = get_connection(sqlite_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM bars WHERE figi = ?", (figi,))
        conn.executemany(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute(
            "UPDATE instrument_metadata SET "
            "  total_bars = (SELECT COUNT(*) FROM bars WHERE figi = ?), "
            "  first_bar_ts = (SELECT MIN(ts)     FROM bars WHERE figi = ?), "
            "  last_bar_ts  = (SELECT MAX(ts)     FROM bars WHERE figi = ?), "
            "  last_run_status = 'ok', "
            "  last_run_at = datetime('now') "
            "WHERE figi = ?",
            (figi, figi, figi, figi),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)


def resolve_figi_for_ticker(sqlite_path: str, symbol: str) -> str | None:
    """Translate a UI symbol (ticker or figi) into a canonical figi.

    Looks up the `instruments` table by ticker first; if no match,
    falls back to figi lookup. Returns `None` when the symbol is
    unknown.
    """
    conn = get_connection(sqlite_path)
    row = conn.execute(
        "SELECT figi FROM instruments WHERE ticker = ? LIMIT 1",
        (symbol,),
    ).fetchone()
    if row:
        return row["figi"]
    row = conn.execute(
        "SELECT figi FROM instruments WHERE figi = ? LIMIT 1",
        (symbol,),
    ).fetchone()
    if row:
        return row["figi"]
    return None


def list_bars(sqlite_path: str, figi: str) -> list[dict]:
    """Return all bars for `figi` ordered by ts ascending.

    Used by `/api/bars/<symbol>` and the migration script.
    """
    conn = get_connection(sqlite_path)
    rows = conn.execute(
        "SELECT ts, open, high, low, close, volume "
        "FROM bars WHERE figi = ? ORDER BY ts",
        (figi,),
    ).fetchall()
    return [
        {
            "ts": str(r["ts"]),
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
        }
        for r in rows
    ]


def count_bars(sqlite_path: str) -> int:
    """Total bars across all figis. Used by `/health`."""
    conn = get_connection(sqlite_path)
    return conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
