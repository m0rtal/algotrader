"""DuckDB connection with OTel instrumentation."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import duckdb

from ..observability.instrumentation import instrument_db_query

_lock = threading.Lock()
_connection: duckdb.DuckDBPyConnection | None = None


def get_connection(bars_dir: str) -> duckdb.DuckDBPyConnection:
    """Get or create an in-memory DuckDB connection with parquet glob pre-registered."""
    global _connection
    with _lock:
        if _connection is None:
            Path(bars_dir).mkdir(parents=True, exist_ok=True)
            conn = duckdb.connect(":memory:")
            glob = f"{bars_dir}/*.parquet"
            try:
                # union_by_name handles schema drift between parquet files
                conn.execute(
                    f"CREATE OR REPLACE VIEW bars AS SELECT * FROM read_parquet('{glob}', hive_partitioning=false, union_by_name=true)"
                )
            except duckdb.IOException:
                conn.execute(
                    "CREATE OR REPLACE VIEW bars AS SELECT "
                    "CAST(NULL AS VARCHAR) AS ticker, CAST(NULL AS DATE) AS ts, "
                    "CAST(NULL AS DOUBLE) AS open, CAST(NULL AS DOUBLE) AS high, "
                    "CAST(NULL AS DOUBLE) AS low, CAST(NULL AS DOUBLE) AS close, "
                    "CAST(NULL AS BIGINT) AS volume, CAST(NULL AS DOUBLE) AS adj_close WHERE 1=0"
                )
            _connection = conn
        return _connection


def close() -> None:
    global _connection
    with _lock:
        if _connection is not None:
            try:
                _connection.close()
            except Exception:
                pass
            _connection = None


def query_bars(
    bars_dir: str,
    ticker: str,
    date_from: str | None = None,
    date_till: str | None = None,
) -> list[dict[str, Any]]:
    """Query OHLCV bars for a ticker. Returns list of dicts with ts/ohlcv/adj_close."""
    conn = get_connection(bars_dir)
    sql = "SELECT ts, open, high, low, close, volume, adj_close FROM bars WHERE ticker = ?"
    params: list[Any] = [ticker]
    if date_from:
        sql += " AND ts >= ?"
        params.append(date_from)
    if date_till:
        sql += " AND ts <= ?"
        params.append(date_till)
    sql += " ORDER BY ts"

    with instrument_db_query(db_system="duckdb", statement=sql) as span:
        span.set_attribute("ticker", ticker)
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        span.set_attribute("db.row_count", len(rows))
        return [dict(zip(cols, r)) for r in rows]


def list_tickers(bars_dir: str) -> list[str]:
    """List tickers available in the bars directory."""
    conn = get_connection(bars_dir)
    with instrument_db_query(db_system="duckdb", statement="SELECT DISTINCT ticker FROM bars ORDER BY ticker") as span:
        rows = conn.execute("SELECT DISTINCT ticker FROM bars ORDER BY ticker").fetchall()
        span.set_attribute("db.row_count", len(rows))
        return [r[0] for r in rows]


def count_bars(bars_dir: str) -> int:
    """Count total bars across all tickers."""
    conn = get_connection(bars_dir)
    with instrument_db_query(db_system="duckdb", statement="SELECT COUNT(*) FROM bars") as span:
        rows = conn.execute("SELECT COUNT(*) FROM bars").fetchall()
        n = rows[0][0] if rows else 0
        span.set_attribute("db.row_count", n)
        return n


def query_ticker_overview(
    bars_dir: str, *, sqlite_path: str | None = None
) -> list[dict[str, Any]]:
    """Per-ticker summary: distinct count of bars + first/last timestamp.

    Powers the Bars tab (StorageTab) — returns one row per ticker with
    enough metadata for the UI to render a useful summary without a
    separate name/sector table (those are still todo upstream).

    Two parquet file shapes coexist on disk:
      * Modern files written by `_atomic_write_parquet` have a
        `ticker` column inside (e.g. `AFKS.parquet`).
      * Legacy figi-style files use the figi as the filename stem
        (e.g. `BBG004730N88.parquet` or a position_uid UUID) and
        have **no** `ticker` column. A plain `GROUP BY ticker`
        silently drops them.

    When `sqlite_path` is provided we resolve the legacy files'
    filenames against the `instruments` table so they appear in
    the result with the right ticker. Without `sqlite_path` the
    legacy files stay hidden (callers that don't want SQL
    dependencies, e.g. tests of the parquet layer, fall through
    to the old behaviour).
    """
    # Read each parquet set freshly rather than reusing the cached
    # `bars` view — the cached view was registered the first time
    # `get_connection` was called for this directory and would not
    # pick up files added since, nor does its union view rebuild
    # when filenames drift in schema.
    glob = f"{bars_dir}/*.parquet"

    def _run(sql: str) -> tuple[list[str], list[tuple]]:
        conn = get_connection(bars_dir)
        with instrument_db_query(db_system="duckdb", statement=sql) as span:
            result = conn.execute(sql).fetchall()
            cols = [d[0] for d in conn.execute(sql).description]
            span.set_attribute("db.row_count", len(result))
        return cols, result

    # Probe whether the union view exposes a `ticker` column. Legacy
    # files don't, so a SELECT ticker FROM ... would BinderError.
    has_ticker_col = False
    try:
        schema_cols, _ = _run(
            f"SELECT * FROM read_parquet('{glob}', "
            f"hive_partitioning=false, union_by_name=true) LIMIT 0"
        )
        has_ticker_col = "ticker" in schema_cols
    except Exception:  # pragma: no cover — empty dir or unreadable parquet
        has_ticker_col = False

    ticker_rows: list[dict[str, Any]] = []
    if has_ticker_col:
        ticker_sql = (
            f"SELECT ticker, COUNT(*) AS bars, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
            f"FROM read_parquet('{glob}', hive_partitioning=false, union_by_name=true) "
            f"WHERE ticker IS NOT NULL GROUP BY ticker"
        )
        cols, rows = _run(ticker_sql)
        ticker_rows = [dict(zip(cols, r)) for r in rows]

    if not sqlite_path:
        return sorted(ticker_rows, key=lambda r: r["ticker"])

    # Legacy fallback: per-file figi walk using DuckDB's filename
    # virtual column. If the union view already has a `ticker`
    # column, only the rows where ticker IS NULL are the legacy
    # ones. Otherwise every row is a legacy file.
    legacy_where = ""
    if has_ticker_col:
        legacy_where = "WHERE ticker IS NULL"
    legacy_sql = (
        f"SELECT filename, COUNT(*) AS bars, MIN(ts) AS first_ts, "
        f"MAX(ts) AS last_ts FROM read_parquet('{glob}', "
        f"hive_partitioning=false, union_by_name=true, filename=true) "
        f"{legacy_where} GROUP BY filename"
    )
    try:
        cols, legacy_rows = _run(legacy_sql)
        legacy = [dict(zip(cols, r)) for r in legacy_rows]
    except Exception:  # pragma: no cover — defensive against any DuckDB binder surprise
        return sorted(ticker_rows, key=lambda r: r["ticker"])

    if not legacy:
        return ticker_rows

    # Resolve filename → figi via stem (filename includes .parquet),
    # then figi → ticker via SQLite. Files where figi is unknown to
    # `instruments` are reported with their figi as ticker so the
    # operator at least sees them in the UI.
    figi_to_ticker: dict[str, str] = {}
    try:
        from .sqlite import execute as _sqlite_exec

        rows = _sqlite_exec(
            sqlite_path,
            "SELECT figi, ticker FROM instruments WHERE ticker IS NOT NULL",
            (),
        )
        figi_to_ticker = {r["figi"]: r["ticker"] for r in rows}
    except Exception:  # pragma: no cover — empty/missing instruments table
        pass

    by_ticker: dict[str, dict[str, Any]] = {r["ticker"]: r for r in ticker_rows}
    for entry in legacy:
        # filename = "<figi>.parquet" → strip suffix
        figi = entry["filename"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        resolved = figi_to_ticker.get(figi, figi)
        if resolved is None:  # pragma: no cover — defensive
            continue
        existing = by_ticker.get(resolved)
        if existing is None:
            by_ticker[resolved] = {
                "ticker": resolved,
                "bars": entry["bars"],
                "first_ts": entry["first_ts"],
                "last_ts": entry["last_ts"],
                "source_figi": figi,
            }
        else:
            # Merge counts/timestamps. We only set `source_figi` on the
            # first legacy row that resolved to this ticker; later
            # rows inherit if the existing one doesn't have it yet
            # (this happens when a modern file was processed before
            # the legacy fallback path).
            if not existing.get("source_figi"):
                existing["source_figi"] = figi
            existing["bars"] = int(existing["bars"]) + int(entry["bars"])
            existing_first = existing["first_ts"]
            existing_last = existing["last_ts"]
            entry_first = entry["first_ts"]
            entry_last = entry["last_ts"]
            if entry_first is not None and (
                existing_first is None or entry_first < existing_first
            ):
                existing["first_ts"] = entry_first
            if entry_last is not None and (
                existing_last is None or entry_last > existing_last
            ):
                existing["last_ts"] = entry_last

    return sorted(by_ticker.values(), key=lambda r: r["ticker"])
