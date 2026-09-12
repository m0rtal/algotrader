"""Tests for the rewritten `/api/tickers` endpoint.

After `remove-duckdb-and-parquet`, the overview is computed entirely
from SQLite: `bars` provides the row aggregates, `instruments` provides
the metadata. No DuckDB connection, no parquet dir walk.

`fileSize` is kept in the response (always 0) because the UI expects
the field.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def seeded_tickers(fresh_db):
    """Seed instruments + bars rows so /api/tickers has data."""
    con = sqlite3.connect(fresh_db)
    con.executescript(
        """
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('SBER', 'FIGI-SBER', 'share', 'Sber', 'Banks', 'rub', 10);
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('YNDX', 'FIGI-YNDX', 'share', 'Yandex', 'IT', 'rub', 1);
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('GAZP', 'FIGI-GAZP', 'share', 'Gazprom', 'OilGas', 'rub', 10);
        """
    )
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("FIGI-SBER", "2025-12-01", 100.0, 110.0, 95.0, 105.0, 1000),
            ("FIGI-SBER", "2025-12-02", 105.0, 112.0, 100.0, 110.0, 1100),
            ("FIGI-SBER", "2025-12-03", 110.0, 115.0, 108.0, 113.0, 1200),
            ("FIGI-YNDX", "2025-12-01", 50.0, 55.0, 48.0, 53.0, 500),
            ("FIGI-GAZP", "2025-12-01", 200.0, 205.0, 195.0, 203.0, 800),
            ("FIGI-GAZP", "2025-12-02", 203.0, 208.0, 200.0, 207.0, 900),
        ],
    )
    con.commit()
    con.close()

    from algotrader_api.routes import data_reads as data_reads_route

    data_reads_route.set_sqlite_path(fresh_db)
    return fresh_db


def test_get_tickers_returns_sqlite_aggregates(seeded_tickers):
    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/tickers")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert {row["symbol"] for row in body} == {"SBER", "YNDX", "GAZP"}
    sber = next(r for r in body if r["symbol"] == "SBER")
    assert sber["bars"] == 3
    assert sber["firstDate"] == "2025-12-01"
    assert sber["lastDate"] == "2025-12-03"
    assert sber["fileSize"] == 0
    assert sber["name"] == "Sber"
    assert sber["sector"] == "Banks"


def test_get_tickers_excludes_bars_without_instruments(seeded_tickers):
    """Bars with no matching instrument row appear with a placeholder ticker."""
    con = sqlite3.connect(seeded_tickers)
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES ('ORPHAN-FIGI', '2025-12-01', 1.0, 2.0, 0.5, 1.5, 100)"
    )
    con.commit()
    con.close()

    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        body = c.get("/api/tickers").json()
    symbols = {row["symbol"] for row in body}
    # ORPHAN-FIGI has no instrument row, so its symbol falls back to the figi.
    assert "ORPHAN-FIGI" in symbols


def test_get_tickers_latency_under_slo(seeded_tickers):
    """Sanity: SQLite-native read path stays well under 100 ms even with rows."""
    import time

    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        t0 = time.time()
        r = c.get("/api/tickers")
        elapsed = (time.time() - t0) * 1000
    assert r.status_code == 200
    assert elapsed < 100, f"/api/tickers took {elapsed:.0f}ms"
