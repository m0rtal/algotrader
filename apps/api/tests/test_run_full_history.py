"""Tests for BackfillRunner.run_full_history — full-history backfill walk.

The full-history runner walks every figi from ``(first_bar_ts - 30d)``
to yesterday, restricted-period-aware and idempotent. These tests cover:

  * Walking every figi in the tradeable universe.
  * Anchoring ``from_`` at ``min(bars.ts) - from_offset_days``.
  * Anchoring ``to_`` at ``today - 1d``.
  * Skipping figis with 0 bars (nothing to anchor on).

The runner is the lifecycle owner: it iterates instruments, decides per
ticker whether to full-backfill or incremental, calls the SDK with rate
limiting, writes bars to parquet, and updates instrument_metadata.

These tests use mocked SDK responses (no real network).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from algotrader_api.ingestion.backfill import BackfillRunner


@pytest.fixture(autouse=True)
def _setup_db(tmp_path):
    """Create only the tables ``run_full_history`` actually touches.

    The project-wide ``run_migrations`` fixture errors on a
    pre-existing schema mismatch (``corporate_actions has no column
    named source``); we only need ``instruments`` and ``bars`` so we
    create them locally to keep these tests independent of that
    baseline failure.
    """
    db_file = str(tmp_path / "state.db")
    con = sqlite3.connect(db_file)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT,
            name TEXT, currency TEXT, lot_size INTEGER, isin TEXT,
            sector TEXT
        );
        CREATE TABLE IF NOT EXISTS bars (
            figi    TEXT NOT NULL,
            ts      DATE NOT NULL,
            open    REAL NOT NULL,
            high    REAL NOT NULL,
            low     REAL NOT NULL,
            close   REAL NOT NULL,
            volume  INTEGER,
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE IF NOT EXISTS instrument_metadata (
            figi TEXT PRIMARY KEY,
            last_bar_ts TEXT,
            last_backfilled_at TEXT,
            total_bars INTEGER,
            last_run_status TEXT,
            last_run_at TEXT,
            last_error TEXT,
            first_bar_ts TEXT
        );
        CREATE TABLE IF NOT EXISTS ingestion_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            run_id INTEGER,
            level TEXT,
            figi TEXT,
            message TEXT
        );
        """
    )
    con.commit()
    con.close()
    yield


def _seed_instrument(db_path: str, ticker: str, figi: str, klass: str = "share") -> None:
    """Insert a row into ``instruments`` (needed by ``_list_instruments``)."""
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ticker, figi, klass, ticker, "rub", 1),
        )
        con.commit()
    finally:
        con.close()


def _seed_bar(db_path: str, figi: str, ts: str) -> None:
    """Insert a single bar row (used to anchor ``min(ts)``)."""
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
            "VALUES (?, ?, 100, 110, 95, 105, 1000)",
            (figi, ts),
        )
        con.commit()
    finally:
        con.close()


def _make_runner(tmp_path, *, client=None) -> BackfillRunner:
    async def noop(ev):
        pass

    return BackfillRunner(
        client=client or MagicMock(),
        db_path=str(tmp_path / "state.db"),
        event_sink=noop,
    )


@pytest.mark.asyncio
async def test_run_full_history_walks_every_figi_in_instruments(tmp_path):
    """Two figis in `instruments`, one bar each. ``_backfill_one`` is
    invoked twice, with a ``from_``/``to_`` pair per figi. The walk
    must cover every row returned by ``_list_instruments``."""
    db = str(tmp_path / "state.db")
    _seed_instrument(db, "S1", "FG1")
    _seed_instrument(db, "S2", "FG2")
    _seed_bar(db, "FG1", "2024-06-15")
    _seed_bar(db, "FG2", "2024-07-20")

    runner = _make_runner(tmp_path)
    # Mock both helpers; we only care that the loop walks both rows.
    runner._list_instruments = MagicMock(
        return_value=[
            {"figi": "FG1", "ticker": "S1", "class": "share"},
            {"figi": "FG2", "ticker": "S2", "class": "share"},
        ]
    )
    runner._backfill_one = AsyncMock(return_value=10)

    processed = await runner.run_full_history()
    assert processed == 2
    assert runner._backfill_one.await_count == 2

    # Each figi was called with kwargs.
    seen_figis = {
        call.kwargs["figi"] for call in runner._backfill_one.await_args_list
    }
    assert seen_figis == {"FG1", "FG2"}

    # ``to_`` must be yesterday for every call.
    for call in runner._backfill_one.await_args_list:
        assert call.kwargs["to"] == date.today() - timedelta(days=1)


@pytest.mark.asyncio
async def test_run_full_history_uses_first_bar_ts_minus_offset_as_from(tmp_path):
    """Figi's earliest bar = 2024-06-15 → from_ = 2024-05-16 (2024-06-15 - 30d)."""
    db = str(tmp_path / "state.db")
    _seed_instrument(db, "S1", "FG1")
    _seed_bar(db, "FG1", "2024-06-15")

    runner = _make_runner(tmp_path)
    runner._list_instruments = MagicMock(
        return_value=[{"figi": "FG1", "ticker": "S1", "class": "share"}]
    )
    runner._backfill_one = AsyncMock(return_value=10)

    await runner.run_full_history(from_offset_days=30)

    assert runner._backfill_one.await_count == 1
    assert runner._backfill_one.await_args is not None
    kwargs = runner._backfill_one.await_args.kwargs
    assert kwargs["figi"] == "FG1"
    assert kwargs["from_"] == date(2024, 5, 16)


@pytest.mark.asyncio
async def test_run_full_history_uses_yesterday_as_to(tmp_path, monkeypatch):
    """``to_`` must equal ``today - 1 day`` regardless of when the test runs."""
    db = str(tmp_path / "state.db")
    _seed_instrument(db, "S1", "FG1")
    _seed_bar(db, "FG1", "2024-06-15")

    fixed_today = date(2026, 1, 15)

    class _FakeDate(date):
        @classmethod
        def today(cls):
            return fixed_today

    monkeypatch.setattr(
        "algotrader_api.ingestion.backfill.date", _FakeDate
    )

    runner = _make_runner(tmp_path)
    runner._list_instruments = MagicMock(
        return_value=[{"figi": "FG1", "ticker": "S1", "class": "share"}]
    )
    runner._backfill_one = AsyncMock(return_value=10)

    await runner.run_full_history()

    assert runner._backfill_one.await_args is not None
    kwargs = runner._backfill_one.await_args.kwargs
    assert kwargs["to"] == fixed_today - timedelta(days=1)


@pytest.mark.asyncio
async def test_run_full_history_skips_figis_with_zero_bars(tmp_path):
    """Figi in `instruments` but 0 rows in `bars` → not passed to ``_backfill_one``."""
    db = str(tmp_path / "state.db")
    _seed_instrument(db, "HAS_BARS", "FG1")
    _seed_instrument(db, "NO_BARS", "FG2")
    _seed_bar(db, "FG1", "2024-06-15")
    # FG2 has no bar rows.

    runner = _make_runner(tmp_path)
    runner._list_instruments = MagicMock(
        return_value=[
            {"figi": "FG1", "ticker": "HAS_BARS", "class": "share"},
            {"figi": "FG2", "ticker": "NO_BARS", "class": "share"},
        ]
    )
    runner._backfill_one = AsyncMock(return_value=10)

    processed = await runner.run_full_history()

    # Only the figi with bars is processed; the one without is skipped.
    assert processed == 1
    assert runner._backfill_one.await_count == 1
    seen = runner._backfill_one.await_args.kwargs["figi"]
    assert seen == "FG1"
    # ``tickers_done`` reflects the actual processed count, not the
    # universe size.
    assert runner.tickers_done == 1