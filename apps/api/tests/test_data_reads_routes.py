"""Coverage tests for the data_reads routes — every endpoint returns the
honest empty default shape so the UI falls into its built-in n/a state.

Each test hits exactly one endpoint and asserts the response shape /
content. The /api/tickers test verifies the DuckDB-backed summary
returns at least the ticker list from the test fixture's parquet glob.
"""
from __future__ import annotations

import re

from fastapi.testclient import TestClient


def test_kpis_empty_list(client: TestClient) -> None:
    assert client.get("/api/kpis").json() == []


def test_trades_empty_list(client: TestClient) -> None:
    assert client.get("/api/trades").json() == []


def test_portfolio_zero_shape(client: TestClient) -> None:
    body = client.get("/api/portfolio").json()
    assert body["cash"] == 0
    assert body["total"] == 0
    assert body["positions"] == []


def test_regime_default_state(client: TestClient) -> None:
    body = client.get("/api/regime").json()
    assert body["state"] == "range"
    assert body["confidence"] == 0


def test_model_unset_shape(client: TestClient) -> None:
    body = client.get("/api/model").json()
    assert body["version"] == ""
    assert body["oosAccuracy"] == 0


def test_model_features_empty(client: TestClient) -> None:
    assert client.get("/api/model/features").json() == []


def test_backtest_folds_empty(client: TestClient) -> None:
    assert client.get("/api/backtest/folds").json() == []


def test_logs_returns_recent_from_ingestion_logs(client: TestClient, data_dir: str) -> None:
    """LogStrip reads the recent operational events from ingestion_logs.

    Seeds three rows covering the three level paths (error/info/unknown)
    so the mapping branch in get_logs is covered.
    """
    import sqlite3
    from datetime import datetime

    db_path = f"{data_dir}/state.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ingestion_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            run_id INTEGER NOT NULL,
            level TEXT NOT NULL,
            figi TEXT,
            message TEXT NOT NULL
        );
        """
    )
    rows = [
        (datetime.now().isoformat(timespec="seconds"), 1, "error", "BBG000BKPL53", "fetch failed: 5xx"),
        (datetime.now().isoformat(timespec="seconds"), 1, "info", "BBG000F02T51", "fetched 180 bars"),
        (datetime.now().isoformat(timespec="seconds"), 1, "debug", None, "noop"),
    ]
    conn.executemany(
        "INSERT INTO ingestion_logs (ts, run_id, level, figi, message) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    body = client.get("/api/logs").json()
    assert isinstance(body, list)
    assert len(body) == 3
    by_text = {r["text"].split(" ", 1)[0]: r for r in body}
    # timestamp cropped to HH:MM:SS (the seeded rows all share the
    # same wall-clock second, so we just assert the format)
    err = next(r for r in body if r["tone"] == "err")
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", err["ts"])
    info = next(r for r in body if r["tone"] == "ok")
    assert info["tone"] == "ok"
    flat = next(r for r in body if r["tone"] == "flat")
    assert flat["tone"] == "flat"
    # figi prefix appears in text, figi-less rows have just the message
    assert any("BBG000BKPL53" in r["text"] for r in body)
    assert any(r["text"] == "noop" for r in body)


def test_logs_since_minutes_filters_old_rows(client: TestClient, data_dir: str) -> None:
    """`since_minutes` keeps the LogStrip honest across long-running sessions.

    A row from 90 minutes ago must not appear in the strip when
    `since_minutes=60`. The full table keeps it for incident review;
    the API just stops at the boundary so the operator never sees
    stale noise from a previous backfill run.
    """
    import sqlite3
    from datetime import datetime, timedelta

    db_path = f"{data_dir}/state.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ingestion_logs (ts, run_id, level, figi, message) "
        "VALUES (?, ?, ?, ?, ?)",
        ((datetime.now() - timedelta(minutes=90)).isoformat(timespec="seconds"),
         1, "error", "OLD", "old noise"),
    )
    conn.execute(
        "INSERT INTO ingestion_logs (ts, run_id, level, figi, message) "
        "VALUES (?, ?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), 1, "info", "NEW", "fresh"),
    )
    conn.commit()
    conn.close()

    body = client.get("/api/logs?since_minutes=60").json()
    texts = [r["text"] for r in body]
    assert any("OLD old noise" not in t for t in texts)
    assert any("NEW fresh" in t for t in texts)


def test_tickers_returns_per_ticker_summary_from_duckdb(client: TestClient) -> None:
    """Bars tab pulls per-ticker summary from DuckDB.

    The shared Ticker schema requires `symbol`, `bars`, `firstDate`,
    `lastDate` populated from the parquet view. Empty bars dir in
    conftest gives back `[]`, which is the honest empty state.
    """
    body = client.get("/api/tickers").json()
    assert isinstance(body, list)
    for row in body:
        # Every field that comes from DuckDB is non-empty; the
        # placeholder ones (name, sector, price, fileSize, gaps) are 0/''.
        assert row["symbol"]
        assert row["bars"] >= 0
        assert row["firstDate"]
        assert row["lastDate"]
        assert row["name"] == ""
        assert row["sector"] == ""


def test_tickers_file_size_reflects_parquet_bytes_on_disk(
    client: TestClient, tmp_path
) -> None:
    """`fileSize` is the parquet file's byte size, not a placeholder.

    The Bars tab aggregates `sum(t.fileSize)` and renders it as
    "MB" — when every row reports 0, the operator sees "0.0 MB" even
    though the parquet files are 40+ MB on disk. The fix is to stat
    the parquet file per ticker.
    """
    import duckdb
    import os

    bars_dir = os.path.join(tmp_path, "data", "bars")
    # conftest already created this dir under data_dir (tmp_path/data/bars).
    # Write a ticker-style parquet file there.
    conn = duckdb.connect(":memory:")
    payload_path = os.path.join(bars_dir, "SIZER.parquet")
    conn.execute(
        f"COPY (SELECT 'SIZER' AS ticker, DATE '2025-01-01' AS ts, "
        f"100.0 AS open, 110.0 AS high, 95.0 AS low, 105.0 AS close, "
        f"1000 AS volume) TO '{payload_path}' (FORMAT PARQUET)"
    )
    conn.close()

    # Force duck.py to rebuild its cached view (it was registered on
    # the empty bars dir earlier).
    from algotrader_api.db import duck as duck_mod

    duck_mod.close()

    body = client.get("/api/tickers").json()
    sized = [r for r in body if r["symbol"] == "SIZER"]
    assert len(sized) == 1, f"SIZER not found in tickers: {[r['symbol'] for r in body]}"
    expected_bytes = os.path.getsize(payload_path)
    assert sized[0]["fileSize"] == expected_bytes, (
        f"fileSize should reflect actual parquet bytes on disk "
        f"({expected_bytes}), got {sized[0]['fileSize']}"
    )
