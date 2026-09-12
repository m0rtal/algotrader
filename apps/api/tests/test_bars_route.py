"""Tests for the `/api/bars/<symbol>` read-path after the bars-SQLite migration.

The endpoint resolves `symbol` (ticker or figi) via `instruments`, then
serves candles from the `bars` SQLite table. No DuckDB, no parquet
directory scan.
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def seeded_bars_db(fresh_db, data_dir):
    """Seed instruments + bars rows so the endpoint has data to serve."""
    con = sqlite3.connect(fresh_db)
    con.executescript(
        """
        INSERT INTO instruments (ticker, figi, class, name, currency, lot_size)
        VALUES ('SBER', 'FIGI-SBER', 'share', 'Sber', 'rub', 10);
        INSERT INTO instruments (ticker, figi, class, name, currency, lot_size)
        VALUES ('YNDX', 'FIGI-YNDX', 'share', 'Yandex', 'rub', 1);
        """
    )
    # Direct INSERT into `bars` (bypasses replace_bars_for_figi to keep
    # this test focused on the read path).
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("FIGI-SBER", "2025-12-01", 100.0, 110.0, 95.0, 105.0, 1000),
            ("FIGI-SBER", "2025-12-02", 105.0, 112.0, 100.0, 110.0, 1100),
            ("FIGI-YNDX", "2025-12-01", 50.0, 55.0, 48.0, 53.0, 500),
        ],
    )
    con.commit()
    con.close()

    # The bars route module reads sqlite_path from a module-level
    # holder injected via lifespan; set it explicitly for tests.
    from algotrader_api.routes import bars as bars_route

    bars_route.set_sqlite_path(fresh_db)
    return fresh_db


def test_bars_endpoint_returns_candles_from_sqlite(seeded_bars_db):
    """`GET /api/bars/SBER` returns rows from the bars table."""
    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/bars/SBER")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "SBER"
    assert body["count"] == 2
    assert [b["t"] for b in body["bars"]] == ["2025-12-01", "2025-12-02"]
    assert body["bars"][0]["o"] == 100.0
    assert body["first"] == "2025-12-01"
    assert body["last"] == "2025-12-02"


def test_bars_endpoint_resolves_by_figi(seeded_bars_db):
    """`/api/bars/FIGI-SBER` works when the symbol is a figi, not a ticker."""
    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/bars/FIGI-SBER")
    assert r.status_code == 200
    assert r.json()["count"] == 2


def test_bars_endpoint_404_for_unknown_symbol(seeded_bars_db):
    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/bars/NOPE")
    assert r.status_code == 404


def test_bars_endpoint_respects_date_filter(seeded_bars_db):
    """`from_` and `till` clamp the result to a window."""
    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/bars/SBER?from_=2025-12-02&till=2025-12-02")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["bars"][0]["t"] == "2025-12-02"
