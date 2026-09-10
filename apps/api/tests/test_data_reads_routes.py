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


def test_logs_empty(client: TestClient) -> None:
    assert client.get("/api/logs").json() == []


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
