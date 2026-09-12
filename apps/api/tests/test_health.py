"""Tests for /health endpoint (SQLite-backed)."""
from __future__ import annotations


def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["sqlite"] == "ok"
    assert body["bars"] == "ok"
    assert body["bars_count"] >= 0


def test_health_response_has_correlation_id(client):
    r = client.get("/health")
    assert "X-Correlation-ID" in r.headers


def test_health_propagates_incoming_correlation_id(client):
    r = client.get("/health", headers={"X-Correlation-ID": "test-cid-123"})
    assert r.headers["X-Correlation-ID"] == "test-cid-123"


def test_health_returns_latency_header(client):
    r = client.get("/health")
    assert "X-Latency-Ms" in r.headers


def test_health_degraded_when_bars_query_broken(client, monkeypatch):
    """When the bars count query raises, health returns 503 with degraded status."""
    from algotrader_api.routes import health

    def broken_count_bars(path):
        raise RuntimeError("simulated bars query failure")

    monkeypatch.setattr(health, "_bars_count", broken_count_bars)
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert "simulated bars query failure" in body["bars"]


def test_health_degraded_when_sqlite_broken(client, monkeypatch):
    """When sqlite.execute raises, status flips to degraded."""
    from algotrader_api.routes import health

    def broken_execute(*a, **k):
        raise RuntimeError("sqlite locked")

    monkeypatch.setattr(health.sqlite, "execute", broken_execute)
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert "sqlite" in body["sqlite"]
