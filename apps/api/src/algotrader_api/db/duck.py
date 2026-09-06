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
