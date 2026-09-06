"""Tests for /api/bars/{symbol} endpoint."""
from __future__ import annotations


def test_get_bars_sber_returns_ohlcv(client):
    r = client.get("/api/bars/SBER")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "SBER"
    assert body["count"] > 0
    bar = body["bars"][0]
    assert set(bar.keys()) == {"t", "o", "h", "l", "c", "v"}


def test_get_bars_unknown_returns_404(client):
    r = client.get("/api/bars/UNKNOWN")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "ticker_not_found"


def test_get_bars_with_date_filter(client):
    r = client.get("/api/bars/SBER", params={"from_": "2025-12-01", "till": "2025-12-31"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 0
    for bar in body["bars"]:
        assert "2025-12-01" <= bar["t"] <= "2025-12-31"


def test_get_bars_lhkh(client):
    r = client.get("/api/bars/LKOH")
    assert r.status_code == 200
    assert r.json()["symbol"] == "LKOH"
    assert r.json()["count"] > 0
