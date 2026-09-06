"""Tests for /health endpoint."""
from __future__ import annotations


def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["sqlite"] == "ok"
    assert body["duckdb"] == "ok"
    assert body["bars_count"] > 0


def test_health_response_has_correlation_id(client):
    r = client.get("/health")
    assert "X-Correlation-ID" in r.headers


def test_health_propagates_incoming_correlation_id(client):
    r = client.get("/health", headers={"X-Correlation-ID": "test-cid-123"})
    assert r.headers["X-Correlation-ID"] == "test-cid-123"


def test_health_returns_latency_header(client):
    r = client.get("/health")
    assert "X-Latency-Ms" in r.headers


def test_health_degraded_when_duckdb_broken(data_dir, monkeypatch):
    """When DuckDB raises, health returns 503 with status=degraded."""
    from algotrader_api.db import duck
    from algotrader_api.main import create_app
    from algotrader_api.routes import health
    from fastapi.testclient import TestClient

    app = create_app()

    def broken_count_bars(bars_dir):
        raise RuntimeError("simulated duckdb failure")

    monkeypatch.setattr(health.duck, "count_bars", broken_count_bars)

    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert "simulated duckdb failure" in body["duckdb"]
