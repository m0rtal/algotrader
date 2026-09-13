"""Tests for the daily data-quality guardian."""
from __future__ import annotations

import asyncio
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
        CREATE TABLE IF NOT EXISTS moex_holidays (
            date TEXT PRIMARY KEY,
            name TEXT NOT NULL
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


def _seed_incomplete_history_figi(db: str, figi: str) -> None:
    """Seed an instrument with sparse history that triggers INCOMPLETE_HISTORY.

    The figi has 1 row in ``instruments`` (matching the migration 002
    schema: ticker, figi, class='share', name, currency='RUB',
    lot_size=1) plus a handful of bars with large calendar gaps, and
    one MOEX holiday in the gap range so ``find_gap_intervals`` has
    something to query.
    """
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO instruments "
            "(ticker, figi, class, name, currency, lot_size) "
            "VALUES (?, ?, 'share', ?, 'RUB', 1)",
            (f"T{figi[-6:]}", figi, "test"),
        )
        # Sparse bars: 3 bars over a long window so the figi triggers
        # both SPARSE_HISTORY and INCOMPLETE_HISTORY in compute_all.
        today = date.today()
        sparse_dates = [
            (today - timedelta(days=400)).isoformat(),
            (today - timedelta(days=200)).isoformat(),
            (today - timedelta(days=2)).isoformat(),
        ]
        con.executemany(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
            "VALUES (?, ?, 1, 1, 1, 1, 1)",
            [(figi, d) for d in sparse_dates],
        )
        con.execute(
            "INSERT INTO instrument_metadata "
            "(figi, last_bar_ts, last_run_status, total_bars) "
            "VALUES (?, ?, 'ok', ?)",
            (figi, (today - timedelta(days=2)).isoformat(), len(sparse_dates)),
        )
        # One holiday in the gap range so the moex_holidays query in
        # find_gap_intervals has something to read.
        con.execute(
            "INSERT INTO moex_holidays (date, name) VALUES (?, 'test')",
            ((today - timedelta(days=300)).isoformat(),),
        )
        con.commit()
    finally:
        con.close()


@pytest.mark.asyncio
async def test_run_daily_guardian_chains_completeness_pass(db, monkeypatch):
    """After recover_stale, the daily guardian runs run_completeness_pass.

    Wires the completeness pass as the next pipeline step (Task 5).
    The pass is replaced with an async spy that records its
    ``reports`` argument and returns an empty CompletenessSummary; we
    assert the spy was called exactly once with the same reports dict
    ``compute_all`` produced upstream.
    """
    from algotrader_api.data_quality import service as svc_mod
    from algotrader_api.data_quality.completeness import CompletenessSummary
    from algotrader_api.data_quality.health import HealthIssue, HealthReport
    from algotrader_api.data_quality.service import run_daily_guardian

    figi = "FIGI-CHAIN"
    _seed_incomplete_history_figi(db, figi)
    # NOTE: we don't pre-build a HealthReport — the spy records the
    # reports dict the upstream compute_all() produces, so the local
    # state is irrelevant to the assertion.

    # Universe sync is mocked out — we only care about the chained pass.
    monkeypatch.setattr(
        "algotrader_api.ingestion.universe.discover_universe",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "algotrader_api.ingestion.universe.upsert_instruments",
        MagicMock(),
    )
    # _make_client_from_settings is the client the guardian threads
    # through into the completeness pass — MagicMock is sufficient
    # because the spy never touches the client.
    monkeypatch.setattr(
        "algotrader_api.data_quality.service._make_client_from_settings",
        MagicMock(),
    )

    called_with: list[dict] = []

    async def _spy(db_, client_, runner_, reports_):
        called_with.append(reports_)
        return CompletenessSummary()

    monkeypatch.setattr(svc_mod, "run_completeness_pass", _spy)

    runner = MagicMock()
    runner.run = MagicMock()  # recover_stale calls it synchronously

    await run_daily_guardian(db, runner)

    assert len(called_with) == 1, (
        "run_completeness_pass must be chained after recover_stale exactly once"
    )
    # The pass receives the same reports dict from the upstream
    # health pass — figi-keyed, same HealthReport instances.
    reports = called_with[0]
    assert figi in reports
    assert reports[figi].figi == figi
    assert HealthIssue.INCOMPLETE_HISTORY in reports[figi].issues

    # A pipeline row for completeness_backfill is appended alongside
    # the guardian_daily row from the existing flow.
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT phase, status, detail FROM pipeline ORDER BY id"
    ).fetchall()
    con.close()
    phases = [r[0] for r in rows]
    assert "completeness_backfill" in phases
    # The completeness row is appended BEFORE the guardian_daily final
    # summary — i.e. the pass runs after recover_stale and its row is
    # written before the guardian rolls everything into one row.
    assert phases.index("completeness_backfill") < phases.index("guardian_daily")
    # The detail string carries the summary counters — even when zero,
    # the operator wants to see the pass ran.
    completeness_row = next(r for r in rows if r[0] == "completeness_backfill")
    assert "examined=" in completeness_row[2]
    assert "gaps=" in completeness_row[2]
