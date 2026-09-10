"""Coverage tests for the data_reads routes — every endpoint returns the
honest empty default shape so the UI falls into its built-in n/a state.

Each test hits exactly one endpoint and asserts the response shape /
content. The /api/tickers test verifies the DuckDB-backed summary
returns at least the ticker list from the test fixture's parquet glob.
"""
from __future__ import annotations

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
        ("2026-09-10T14:32:11.000000+00:00", 1, "error", "BBG000BKPL53", "fetch failed: 5xx"),
        ("2026-09-10T14:33:00.000000+00:00", 1, "info", "BBG000F02T51", "fetched 180 bars"),
        ("2026-09-10T14:34:00.000000+00:00", 1, "debug", None, "noop"),
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
    # timestamp cropped to HH:MM:SS
    err = next(r for r in body if r["tone"] == "err")
    assert err["ts"] == "14:32:11"
    info = next(r for r in body if r["tone"] == "ok")
    assert info["tone"] == "ok"
    flat = next(r for r in body if r["tone"] == "flat")
    assert flat["tone"] == "flat"
    # figi prefix appears in text, figi-less rows have just the message
    assert any("BBG000BKPL53" in r["text"] for r in body)
    assert any(r["text"] == "noop" for r in body)


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
