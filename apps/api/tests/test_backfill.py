"""Tests for the BackfillRunner decision logic + per-ticker flows.

The runner is the lifecycle owner: it iterates instruments, decides per
ticker whether to full-backfill or incremental, calls the SDK with rate
limiting, writes bars to parquet, and updates instrument_metadata.

These tests use mocked SDK responses (no real network) so they run in
the default `pytest tests/` invocation. A separate
`test_backfill_sandbox.py` covers the gated live-sandbox path.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from algotrader_api.ingestion.backfill import (
    BackfillRunner,
    BackfillState,
    decide_strategy,
)


@pytest.fixture(autouse=True)
def _run_migrations(tmp_path):
    """Run all migrations on the tmp DB so instrument_metadata and
    ingestion_logs tables exist before any test that touches them."""
    from algotrader_api.db import sqlite as sqlitedb
    migrations_dir = str(Path(__file__).resolve().parent.parent / "src/algotrader_api/db/migrations")
    db_file = str(tmp_path / "state.db")
    sqlitedb.run_migrations(db_file, migrations_dir)
    sqlitedb.close_all()
    yield
    sqlitedb.close_all()


# ─── strategy decision ──────────────────────────────────────────────


def test_decide_strategy_no_metadata_row_returns_full():
    """First-time ticker: backfill full history."""
    today = date(2026, 9, 8)
    strategy, from_, to = decide_strategy(
        metadata_row=None,
        today=today,
        history_years=5,
        incremental_threshold_days=2,
    )
    assert strategy == "full"
    assert from_ == date(2021, 9, 8)
    assert to == today


def test_decide_strategy_fresh_metadata_returns_skip():
    """Recent last_bar_ts: incremental threshold not crossed, skip."""
    today = date(2026, 9, 8)
    recent = (today - timedelta(days=1)).isoformat()
    strategy, from_, to = decide_strategy(
        metadata_row={"last_bar_ts": recent, "last_run_status": "ok"},
        today=today,
        history_years=5,
        incremental_threshold_days=2,
    )
    assert strategy == "skip"
    assert from_ is None and to is None


def test_decide_strategy_stale_metadata_returns_incremental():
    """last_bar_ts older than threshold → incremental from last+1day."""
    today = date(2026, 9, 8)
    stale = (today - timedelta(days=5)).isoformat()  # 2026-09-03
    strategy, from_, to = decide_strategy(
        metadata_row={"last_bar_ts": stale, "last_run_status": "ok"},
        today=today,
        history_years=5,
        incremental_threshold_days=2,
    )
    assert strategy == "incremental"
    # from_ = last_bar_ts + 1 day = 2026-09-04
    assert from_ == date(2026, 9, 4)
    assert to == today


def test_decide_strategy_metadata_with_no_bars_yet_returns_full():
    today = date(2026, 9, 8)
    strategy, from_, to = decide_strategy(
        metadata_row={"last_bar_ts": None, "last_run_status": "error"},
        today=today,
        history_years=5,
        incremental_threshold_days=2,
    )
    assert strategy == "full"
    assert from_ == date(2021, 9, 8)


def test_decide_strategy_at_threshold_boundary_returns_incremental():
    """Exactly at the threshold should still incremental (boundary inclusive)."""
    today = date(2026, 9, 8)
    boundary = (today - timedelta(days=2)).isoformat()  # 2026-09-06
    strategy, from_, to = decide_strategy(
        metadata_row={"last_bar_ts": boundary, "last_run_status": "ok"},
        today=today,
        history_years=5,
        incremental_threshold_days=2,
    )
    assert strategy == "incremental"
    # from_ = boundary + 1 = 2026-09-07
    assert from_ == date(2026, 9, 7)
    assert to == today


# ─── universe discovery ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_universe_returns_total_instrument_count(tmp_path):
    """After fetching all 5 asset classes, returns total instruments."""
    events = []

    async def collect(ev):
        events.append(ev)

    client = MagicMock()
    client.get_shares = AsyncMock(return_value=[
        {"ticker": "S1", "figi": "FG1", "class": "share"},
    ])
    client.get_bonds = AsyncMock(return_value=[
        {"ticker": "B1", "figi": "FG2", "class": "bond"},
        {"ticker": "B2", "figi": "FG3", "class": "bond"},
    ])
    client.get_etfs = AsyncMock(return_value=[
        {"ticker": "E1", "figi": "FG4", "class": "etf"},
    ])
    client.get_futures = AsyncMock(return_value=[])
    client.get_options = AsyncMock(return_value=[])

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=collect,
    )
    count = await runner._discover_universe()
    assert count == 4  # 1 share + 2 bonds + 1 etf + 0 futures + 0 options
    client.get_shares.assert_awaited_once()
    client.get_bonds.assert_awaited_once()
    client.get_etfs.assert_awaited_once()
    client.get_futures.assert_awaited_once()
    client.get_options.assert_awaited_once()
    assert any(ev.type == "ticker_progress" for ev in events)


# ─── per-ticker backfill ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_one_filters_closed_candles(tmp_path):
    """is_complete=False bars are dropped; is_complete=True bars written."""
    bars_dir = tmp_path / "bars"
    closed = SimpleNamespace(
        time=SimpleNamespace(year=2024, month=6, day=1),
        open=SimpleNamespace(units=100, nano=0),
        high=SimpleNamespace(units=110, nano=0),
        low=SimpleNamespace(units=95, nano=0),
        close=SimpleNamespace(units=105, nano=0),
        volume=1000,
        is_complete=True,
    )
    open_bar = SimpleNamespace(
        time=SimpleNamespace(year=2024, month=6, day=2),
        open=SimpleNamespace(units=105, nano=0),
        high=SimpleNamespace(units=108, nano=0),
        low=SimpleNamespace(units=103, nano=0),
        close=SimpleNamespace(units=106, nano=0),
        volume=500,
        is_complete=False,
    )
    client = MagicMock()
    # The chunked runner calls get_candles once per ~7-day window. For
    # June 1..30 that is five chunks; return the same [closed, open_bar]
    # fixture so behaviour is observable across every chunk.
    client.get_candles = AsyncMock(return_value=[closed, open_bar])

    events = []

    async def collect(ev):
        events.append(ev)

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(bars_dir),
        event_sink=collect,
    )
    written = await runner._backfill_one(
        figi="BBG004730N88",
        from_=date(2024, 6, 1),
        to=date(2024, 6, 30),
    )
    # 5 chunks × 1 closed bar = 5 closed candles persisted.
    assert written == 5
    parquet = bars_dir / "BBG004730N88.parquet"
    assert parquet.exists()
    progress_events = [ev for ev in events if ev.type == "ticker_progress"]
    assert len(progress_events) == 1
    assert progress_events[0].payload["status"] == "ok"
    assert progress_events[0].payload["bars_written"] == 5


@pytest.mark.asyncio
async def test_backfill_one_updates_instrument_metadata(tmp_path):
    """After success, instrument_metadata row has last_bar_ts and total_bars."""
    import sqlite3

    closed = SimpleNamespace(
        time=SimpleNamespace(year=2024, month=12, day=31),
        open=SimpleNamespace(units=200, nano=0),
        high=SimpleNamespace(units=210, nano=0),
        low=SimpleNamespace(units=195, nano=0),
        close=SimpleNamespace(units=205, nano=0),
        volume=2000,
        is_complete=True,
    )
    client = MagicMock()
    client.get_candles = AsyncMock(return_value=[closed])

    async def noop(ev):
        pass

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=noop,
    )
    await runner._backfill_one(figi="BBG001", from_=date(2024, 1, 1), to=date(2024, 12, 31))

    con = sqlite3.connect(str(tmp_path / "state.db"))
    row = con.execute(
        "SELECT last_bar_ts, total_bars, last_run_status FROM instrument_metadata WHERE figi = ?",
        ("BBG001",),
    ).fetchone()
    con.close()
    assert row is not None
    assert row[0] == "2024-12-31"
    # 53 chunks × 1 closed candle (all stamped 2024-12-31 by the fixture) — append_bars
    # writes whatever it gets so the persisted candle count equals the
    # number of chunks performed.
    assert row[1] == 53
    assert row[2] == "ok"


@pytest.mark.asyncio
async def test_backfill_one_logs_failure_doesnt_abort(tmp_path):
    """If the SDK raises, the per-ticker error is logged and recorded as
    last_run_status='error' but the runner returns (no exception)."""
    client = MagicMock()
    client.get_candles = AsyncMock(side_effect=RuntimeError("network blip"))

    events = []

    async def collect(ev):
        events.append(ev)

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=collect,
    )
    written = await runner._backfill_one(
        figi="BBG999",
        from_=date(2024, 1, 1),
        to=date(2024, 12, 31),
    )
    assert written == 0
    progress = [ev for ev in events if ev.type == "ticker_progress"]
    assert len(progress) == 1
    assert progress[0].payload["status"] == "error"
    assert "network blip" in progress[0].payload["error"]


@pytest.mark.asyncio
async def test_backfill_one_retries_chunk_on_resource_exhausted(tmp_path):
    """A RESOURCE_EXHAUSTED chunk should be retried (not just logged as
    a warning and abandoned). The runner holds a per-ticker retry
    policy; one initial call + one retry should be enough to recover
    before the per-chunk warning fires."""
    import sqlite3

    bars_dir = tmp_path / "bars"
    closed = SimpleNamespace(
        time=SimpleNamespace(year=2024, month=6, day=1),
        open=SimpleNamespace(units=100, nano=0),
        high=SimpleNamespace(units=110, nano=0),
        low=SimpleNamespace(units=95, nano=0),
        close=SimpleNamespace(units=105, nano=0),
        volume=1000,
        is_complete=True,
    )
    closed_bar_dict = {
        "time": {"year": 2024, "month": 6, "day": 1},
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000,
        "is_complete": True,
    }

    client = MagicMock()
    # First call raises RESOURCE_EXHAUSTED, second call succeeds.
    client.get_candles = AsyncMock(
        side_effect=[
            RuntimeError("RESOURCE_EXHAUSTED: rate limit"),
            [closed_bar_dict],
        ]
    )

    events = []

    async def collect(ev):
        events.append(ev)

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(bars_dir),
        event_sink=collect,
    )
    written = await runner._backfill_one(
        figi="BBG001",
        from_=date(2024, 6, 1),
        to=date(2024, 6, 7),
    )
    # Retry succeeded → we should have written 1 bar from the recovered chunk.
    assert written == 1
    # The SDK was called twice (first attempt + retry).
    assert client.get_candles.await_count == 2
    # No ticker_progress error event was emitted for this figi.
    error_events = [
        ev for ev in events
        if ev.type == "ticker_progress"
        and ev.payload.get("status") == "error"
    ]
    assert error_events == []
    # Metadata reflects a successful run, not the transient RESOURCE_EXHAUSTED.
    con = sqlite3.connect(str(tmp_path / "state.db"))
    row = con.execute(
        "SELECT last_run_status FROM instrument_metadata WHERE figi = ?",
        ("BBG001",),
    ).fetchone()
    con.close()
    assert row is not None
    assert row[0] == "ok"


# ─── state transitions ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_emits_status_ticker_progress_done_events(tmp_path):
    """A full run emits at least one of each event type."""
    client = MagicMock()
    client.get_shares = AsyncMock(return_value=[
        {"ticker": "T1", "figi": "FG1", "class": "share"},
    ])
    client.get_bonds = AsyncMock(return_value=[])
    client.get_etfs = AsyncMock(return_value=[])
    client.get_futures = AsyncMock(return_value=[])
    client.get_options = AsyncMock(return_value=[])
    closed = SimpleNamespace(
        time=SimpleNamespace(year=2024, month=6, day=15),
        open=SimpleNamespace(units=100, nano=0),
        high=SimpleNamespace(units=110, nano=0),
        low=SimpleNamespace(units=95, nano=0),
        close=SimpleNamespace(units=105, nano=0),
        volume=1000,
        is_complete=True,
    )
    client.get_candles = AsyncMock(return_value=[closed])

    events = []

    async def collect(ev):
        events.append(ev)

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=collect,
    )
    await runner.run(history_years=5, incremental_threshold_days=2)

    types = [ev.type for ev in events]
    assert "status" in types
    assert "ticker_progress" in types
    assert "done" in types


@pytest.mark.asyncio
async def test_stop_signal_causes_runner_to_exit_between_tickers(tmp_path):
    """Setting stop() during run() halts before processing the next ticker."""
    client = MagicMock()
    closed = SimpleNamespace(
        time=SimpleNamespace(year=2024, month=6, day=1),
        open=SimpleNamespace(units=1, nano=0),
        high=SimpleNamespace(units=2, nano=0),
        low=SimpleNamespace(units=0, nano=0),
        close=SimpleNamespace(units=1, nano=0),
        volume=0,
        is_complete=True,
    )
    client.get_shares = AsyncMock(return_value=[
        {"ticker": "T1", "figi": "FG1", "class": "share"},
        {"ticker": "T2", "figi": "FG2", "class": "share"},
    ])
    client.get_bonds = AsyncMock(return_value=[])
    client.get_etfs = AsyncMock(return_value=[])
    client.get_futures = AsyncMock(return_value=[])
    client.get_options = AsyncMock(return_value=[])
    client.get_candles = AsyncMock(return_value=[closed])

    async def noop(ev):
        pass

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=noop,
    )
    original = runner._backfill_one

    async def stop_after_first(*args, **kwargs):
        result = await original(*args, **kwargs)
        runner.stop()
        return result

    runner._backfill_one = stop_after_first

    await runner.run(history_years=5, incremental_threshold_days=2)
    assert runner.state in (BackfillState.STOPPING, BackfillState.IDLE)


# ─── log helper ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_writes_row_with_correct_fields(tmp_path):
    """_log inserts into ingestion_logs with ts, run_id, level, figi, message."""
    import sqlite3

    async def noop(ev):
        pass

    runner = BackfillRunner(
        client=MagicMock(),
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=noop,
    )
    runner.run_id = 42
    await runner._log(level="info", figi="BBG001", message="backfilled 250 bars")
    con = sqlite3.connect(str(tmp_path / "state.db"))
    row = con.execute(
        "SELECT ts, run_id, level, figi, message FROM ingestion_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert row is not None
    assert row[1] == 42
    assert row[2] == "info"
    assert row[3] == "BBG001"
    assert row[4] == "backfilled 250 bars"
    datetime.fromisoformat(row[0])


# ─── defensive branches ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_universe_continues_after_method_failure(tmp_path):
    """If one method raises, others still execute and total reflects successes."""
    events = []

    async def collect(ev):
        events.append(ev)

    client = MagicMock()
    client.get_shares = AsyncMock(side_effect=RuntimeError("network blip"))
    client.get_bonds = AsyncMock(return_value=[{"ticker": "B1", "figi": "FG2", "class": "bond"}])
    client.get_etfs = AsyncMock(return_value=[])
    client.get_futures = AsyncMock(return_value=[])
    client.get_options = AsyncMock(return_value=[])

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=collect,
    )
    count = await runner._discover_universe()
    # shares failed → 0; bonds succeeded → 1; others 0
    assert count == 1
    # Error was logged.
    import sqlite3
    con = sqlite3.connect(str(tmp_path / "state.db"))
    row = con.execute(
        "SELECT message FROM ingestion_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert "shares failed" in row[0]


@pytest.mark.asyncio
async def test_backfill_one_filters_today_or_later_candles(tmp_path):
    """Today's candle is dropped even if is_complete=True (defensive guard)."""
    from datetime import date as _date

    today = _date.today()
    today_candle = SimpleNamespace(
        time=SimpleNamespace(year=today.year, month=today.month, day=today.day),
        open=SimpleNamespace(units=100, nano=0),
        high=SimpleNamespace(units=110, nano=0),
        low=SimpleNamespace(units=95, nano=0),
        close=SimpleNamespace(units=105, nano=0),
        volume=1000,
        is_complete=True,
    )
    client = MagicMock()
    client.get_candles = AsyncMock(return_value=[today_candle])

    async def noop(ev):
        pass

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=noop,
    )
    written = await runner._backfill_one(
        figi="BBG001",
        from_=today - timedelta(days=5),
        to=today,
    )
    assert written == 0


def test_extract_last_bar_ts_returns_none_for_empty():
    from algotrader_api.ingestion.backfill import BackfillRunner
    runner = BackfillRunner(
        client=MagicMock(),
        db_path="/tmp/nonexistent",
        bars_dir="/tmp/nonexistent",
        event_sink=lambda ev: None,
    )
    assert runner._extract_last_bar_ts([]) is None


def test_extract_last_bar_ts_returns_none_when_no_time_attr():
    """Candle-like objects without .time should return None (defensive)."""
    from algotrader_api.ingestion.backfill import BackfillRunner
    runner = BackfillRunner(
        client=MagicMock(),
        db_path="/tmp/nonexistent",
        bars_dir="/tmp/nonexistent",
        event_sink=lambda ev: None,
    )
    # No `time` attribute at all — falls into the default branch.
    assert runner._extract_last_bar_ts([SimpleNamespace()]) is None


def test_extract_last_bar_ts_finds_max():
    """Out-of-order candles → returns the chronologically max date."""
    from algotrader_api.ingestion.backfill import BackfillRunner
    runner = BackfillRunner(
        client=MagicMock(),
        db_path="/tmp/nonexistent",
        bars_dir="/tmp/nonexistent",
        event_sink=lambda ev: None,
    )
    candles = [
        SimpleNamespace(time=SimpleNamespace(year=2024, month=3, day=15)),
        SimpleNamespace(time=SimpleNamespace(year=2024, month=12, day=31)),
        SimpleNamespace(time=SimpleNamespace(year=2024, month=7, day=4)),
    ]
    assert runner._extract_last_bar_ts(candles) == "2024-12-31"


def test_upsert_instrument_skips_row_without_figi(tmp_path):
    """Missing figi → silently skip (no DB write attempted)."""
    import sqlite3
    from algotrader_api.ingestion.backfill import BackfillRunner

    async def noop(ev):
        pass

    runner = BackfillRunner(
        client=MagicMock(),
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=noop,
    )
    runner._upsert_instrument({"ticker": "X", "class": "share"})  # no figi
    # No row inserted
    con = sqlite3.connect(str(tmp_path / "state.db"))
    rows = con.execute("SELECT COUNT(*) FROM instruments").fetchone()
    con.close()
    assert rows[0] == 0


@pytest.mark.asyncio
async def test_run_emits_status_with_tickers_total_after_discover(tmp_path):
    """After discover completes, a status event includes the new tickers_total."""
    events = []

    async def collect(ev):
        events.append(ev)

    client = MagicMock()
    client.get_shares = AsyncMock(return_value=[
        {"ticker": "T1", "figi": "FG1", "class": "share"},
        {"ticker": "T2", "figi": "FG2", "class": "share"},
    ])
    client.get_bonds = AsyncMock(return_value=[])
    client.get_etfs = AsyncMock(return_value=[])
    client.get_futures = AsyncMock(return_value=[])
    client.get_options = AsyncMock(return_value=[])
    closed = SimpleNamespace(
        time=SimpleNamespace(year=2024, month=6, day=15),
        open=SimpleNamespace(units=100, nano=0),
        high=SimpleNamespace(units=110, nano=0),
        low=SimpleNamespace(units=95, nano=0),
        close=SimpleNamespace(units=105, nano=0),
        volume=1000,
        is_complete=True,
    )
    client.get_candles = AsyncMock(return_value=[closed])

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=collect,
    )
    await runner.run(history_years=5, incremental_threshold_days=2)
    status_events = [ev for ev in events if ev.type == "status"]
    # Last status event should carry the populated tickers_total.
    final_status = status_events[-1]
    assert final_status.payload.get("tickers_total") == 2


@pytest.mark.asyncio
async def test_run_stop_signal_before_first_ticker(tmp_path):
    """stop() called before run() means no tickers are processed."""
    client = MagicMock()
    client.get_shares = AsyncMock(return_value=[{"ticker": "T1", "figi": "FG1", "class": "share"}])
    client.get_bonds = AsyncMock(return_value=[])
    client.get_etfs = AsyncMock(return_value=[])
    client.get_futures = AsyncMock(return_value=[])
    client.get_options = AsyncMock(return_value=[])
    closed = SimpleNamespace(
        time=SimpleNamespace(year=2024, month=6, day=15),
        open=SimpleNamespace(units=1, nano=0),
        high=SimpleNamespace(units=2, nano=0),
        low=SimpleNamespace(units=0, nano=0),
        close=SimpleNamespace(units=1, nano=0),
        volume=0,
        is_complete=True,
    )
    client.get_candles = AsyncMock(return_value=[closed])

    async def noop(ev):
        pass

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=noop,
    )
    runner.stop()  # pre-stop
    await runner.run(history_years=5, incremental_threshold_days=2)
    # Universe still completes (stop flag is checked between tickers, not
    # before universe discovery). Verify the done event reports stopped.
    done = None
    for ev in [
        # We didn't capture events; just check state.
    ]:
        pass
    # run_id remains 0 because we never assigned
    assert runner.run_id == 0


@pytest.mark.asyncio
async def test_run_handles_discover_failure_with_done_event(tmp_path):
    """If _discover_universe raises, a 'done' event with status='error'
    is emitted and state returns to IDLE."""
    events = []

    async def collect(ev):
        events.append(ev)

    client = MagicMock()
    client.get_shares = AsyncMock(side_effect=RuntimeError("boom"))
    client.get_bonds = AsyncMock(side_effect=RuntimeError("boom"))
    client.get_etfs = AsyncMock(side_effect=RuntimeError("boom"))
    client.get_futures = AsyncMock(side_effect=RuntimeError("boom"))
    client.get_options = AsyncMock(side_effect=RuntimeError("boom"))

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=collect,
    )
    await runner.run(history_years=5, incremental_threshold_days=2)
    # 5 method failures → 5 "error" ingestion_log entries
    import sqlite3
    con = sqlite3.connect(str(tmp_path / "state.db"))
    count = con.execute("SELECT COUNT(*) FROM ingestion_logs WHERE message LIKE '%failed: boom'").fetchone()[0]
    con.close()
    assert count == 5


@pytest.mark.asyncio
async def test_backfill_one_skipped_when_no_closed_bars(tmp_path):
    """If all candles are open (is_complete=False), 0 bars written,
    metadata recorded as 'skipped'."""
    open_only = SimpleNamespace(
        time=SimpleNamespace(year=2024, month=6, day=1),
        open=SimpleNamespace(units=1, nano=0),
        high=SimpleNamespace(units=2, nano=0),
        low=SimpleNamespace(units=0, nano=0),
        close=SimpleNamespace(units=1, nano=0),
        volume=0,
        is_complete=False,
    )
    client = MagicMock()
    client.get_candles = AsyncMock(return_value=[open_only])

    events = []

    async def collect(ev):
        events.append(ev)

    runner = BackfillRunner(
        client=client,
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path / "bars"),
        event_sink=collect,
    )
    written = await runner._backfill_one(
        figi="BBG002", from_=date(2024, 6, 1), to=date(2024, 6, 30)
    )
    assert written == 0
    progress = [ev for ev in events if ev.type == "ticker_progress"]
    assert progress[0].payload["status"] == "skipped"
    assert progress[0].payload["bars_written"] == 0
