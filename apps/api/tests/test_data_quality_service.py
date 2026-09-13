"""Tests for the daily data-quality guardian."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    con = sqlite3.connect(p)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT,
            name TEXT, currency TEXT, lot_size INTEGER, isin TEXT,
            sector TEXT, source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS instrument_metadata (
            figi TEXT PRIMARY KEY, last_bar_ts TEXT,
            last_backfilled_at TEXT, total_bars INTEGER,
            last_run_status TEXT, last_run_at TEXT, last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS bars (
            figi TEXT NOT NULL, ts TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE IF NOT EXISTS pipeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phase TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            rows_processed INTEGER DEFAULT 0,
            status VARCHAR DEFAULT 'ok',
            detail TEXT
        );
        """
    )
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES ('HEALTHY', 'FIGI-HEALTHY', 'share', 'H', 'rub', 1), "
        "       ('STALE',   'FIGI-STALE',   'share', 'S', 'rub', 1)"
    )
    today = date.today()
    # FIGI-HEALTHY: bars up to today.
    healthy_dates = [(today - timedelta(days=i)).isoformat() for i in range(60, -1, -1)]
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, 1, 1, 1, 1, 1)",
        [("FIGI-HEALTHY", d) for d in healthy_dates],
    )
    # FIGI-STALE: last bar 30 days ago.
    stale_dates = [(today - timedelta(days=i)).isoformat() for i in range(60, 30, -1)]
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, 1, 1, 1, 1, 1)",
        [("FIGI-STALE", d) for d in stale_dates],
    )
    con.execute(
        "INSERT INTO instrument_metadata (figi, last_bar_ts, last_run_status, total_bars) "
        "VALUES ('FIGI-STALE', ?, 'ok', ?)",
        ((today - timedelta(days=30)).isoformat(), len(stale_dates)),
    )
    con.commit()
    con.close()
    return p


@pytest.mark.asyncio
async def test_run_daily_guardian_full_cycle(db, monkeypatch):
    """Universe sync + health + recovery + pipeline row in one run."""
    runner = MagicMock()
    runner.run = MagicMock()  # recover_stale calls it synchronously

    monkeypatch.setattr("algotrader_api.ingestion.universe.discover_universe",
                        AsyncMock(return_value=[]))
    monkeypatch.setattr("algotrader_api.ingestion.universe.upsert_instruments",
                        MagicMock())
    monkeypatch.setattr("algotrader_api.data_quality.service._make_client_from_settings",
                        MagicMock())

    from algotrader_api.data_quality.service import run_daily_guardian

    summary = await run_daily_guardian(db, runner)

    assert summary.figis_checked == 2
    # Only FIGI-STALE should be queued (FIGI-HEALTHY is at 100).
    runner.run.assert_called_once()
    kwargs = runner.run.call_args.kwargs
    assert kwargs["limit_to"] == ["FIGI-STALE"]

    # Pipeline row recorded.
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT phase, status, detail FROM pipeline ORDER BY id DESC LIMIT 1"
    ).fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0][0] == "guardian_daily"
    assert rows[0][1] == "ok"
    assert "figis_checked=2" in rows[0][2]


@pytest.mark.asyncio
async def test_run_daily_guardian_skips_when_all_healthy(db, monkeypatch):
    """All-healthy universe: empty queue, no runner call."""
    # Wipe the stale figi.
    con = sqlite3.connect(db)
    con.execute("DELETE FROM bars WHERE figi='FIGI-STALE'")
    today = date.today()
    healthy_dates = [(today - timedelta(days=i)).isoformat() for i in range(60, -1, -1)]
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES ('FIGI-STALE', ?, 1, 1, 1, 1, 1)",
        [(d,) for d in healthy_dates],
    )
    con.execute(
        "UPDATE instrument_metadata SET last_bar_ts=?, total_bars=200 WHERE figi='FIGI-STALE'",
        (today.isoformat(),),
    )
    con.commit()
    con.close()

    runner = MagicMock()
    runner.run = MagicMock()

    monkeypatch.setattr("algotrader_api.ingestion.universe.discover_universe",
                        AsyncMock(return_value=[]))
    monkeypatch.setattr("algotrader_api.ingestion.universe.upsert_instruments",
                        MagicMock())
    monkeypatch.setattr("algotrader_api.data_quality.service._make_client_from_settings",
                        MagicMock())

    from algotrader_api.data_quality.service import run_daily_guardian

    summary = await run_daily_guardian(db, runner)

    assert summary.figis_checked == 2
    assert summary.figis_recovered == 0
    runner.run.assert_not_called()
