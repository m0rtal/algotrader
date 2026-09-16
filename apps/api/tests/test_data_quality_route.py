"""Tests for /api/data-quality/{symbol} drill-down endpoint."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    con = sqlite3.connect(p)
    con.executemany(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES (?, ?, 'share', ?, 'rub', 1)",
        [
            ("SBER", "FIGI-SBER", "Sber"),
            ("OFZ", "FIGI-OFZ", "Ofz"),
            ("GOOD", "FIGI-GOOD", "Good"),
            ("BAD", "FIGI-BAD", "Bad"),
        ],
    )
    today = date.today()
    # FIGI-SBER healthy, FIGI-OFZ healthy (also), FIGI-GOOD healthy, FIGI-BAD stale.
    healthy = [(today - timedelta(days=i)).isoformat() for i in range(60, -1, -1)]
    for figi in ("FIGI-SBER", "FIGI-OFZ", "FIGI-GOOD"):
        con.executemany(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
            "VALUES (?, ?, 1, 1, 1, 1, 1)",
            [(figi, d) for d in healthy],
        )
    # BAD: last bar 10 days ago.
    stale = [(today - timedelta(days=i)).isoformat() for i in range(60, 10, -1)]
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES ('FIGI-BAD', ?, 1, 1, 1, 1, 1)",
        [(d,) for d in stale],
    )
    con.execute(
        "INSERT INTO ingestion_logs (ts, run_id, level, figi, message) "
        "VALUES (datetime('now', '-1 day'), 1, 'warn', 'FIGI-BAD', 'rate limit hit')"
    )
    con.commit()
    con.close()
    return p


def _wire(client, db):
    """Make the lifespan path inject our fresh_db into the route's holders."""
    from algotrader_api.routes import data_reads as dr
    from algotrader_api.routes import bars as br
    dr.set_sqlite_path(db)
    br.set_sqlite_path(db)


def test_get_data_quality_returns_health_report_for_healthy_ticker(client, db):
    _wire(client, db)
    r = client.get("/api/data-quality/SBER")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "SBER"
    assert body["figi"] == "FIGI-SBER"
    assert body["health_score"] == 100
    assert body["issues"] == []
    assert body["actual_bars"] > 0


def test_get_data_quality_returns_health_report_for_unhealthy_ticker(client, db):
    _wire(client, db)
    r = client.get("/api/data-quality/BAD")
    assert r.status_code == 200
    body = r.json()
    assert body["health_score"] <= 70
    assert "missing-recent-days" in body["issues"]
    assert "rate-limited-failures" in body["issues"]
    assert len(body["recent_failures"]) > 0


def test_get_data_quality_returns_404_for_unknown_symbol(client, db):
    _wire(client, db)
    r = client.get("/api/data-quality/UNKNOWN_TICKER_XYZ")
    assert r.status_code == 404


def test_get_data_quality_resolves_figi_alias(client, db):
    _wire(client, db)
    r = client.get("/api/data-quality/FIGI-GOOD")
    assert r.status_code == 200
    body = r.json()
    assert body["figi"] == "FIGI-GOOD"
    assert body["ticker"] == "GOOD"


def test_get_data_quality_serializes_incomplete_history(client, db):
    """Sparse figi: actual < expected * 0.95 → INCOMPLETE_HISTORY surfaces as
    "incomplete-history" in the wire-format issues list."""
    import sqlite3
    today = date(2026, 9, 12)
    sparse = [(today - timedelta(days=i)).isoformat() for i in range(120, 0, -8)]
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES ('SPARSE', 'FIGI-SPARSE', 'share', 'Sparse', 'rub', 1)"
    )
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES ('FIGI-SPARSE', ?, 1, 1, 1, 1, 1)",
        [(d,) for d in sparse],
    )
    con.commit()
    con.close()
    _wire(client, db)
    r = client.get("/api/data-quality/SPARSE")
    assert r.status_code == 200
    body = r.json()
    assert "incomplete-history" in body["issues"]
    assert body["health_score"] <= 75  # -25 penalty for INCOMPLETE_HISTORY
