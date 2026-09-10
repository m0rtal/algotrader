"""Coverage tests for BackfillRunner.run() skipped + stopped branches."""

import asyncio
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from algotrader_api.ingestion import backfill as bf_mod
from algotrader_api.ingestion.backfill import BackfillRunner, BackfillEvent


def _seed_instruments_and_metadata(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    con.execute(
        """CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE,
            class TEXT, name TEXT,
            currency TEXT, lot_size INTEGER, isin TEXT, sector TEXT,
            source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS instrument_metadata (
            figi TEXT PRIMARY KEY,
            last_bar_ts TEXT,
            last_run_at TEXT,
            total_bars INTEGER,
            last_run_status TEXT,
            last_error TEXT
        )"""
    )
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) VALUES (?, ?, ?, ?, ?, ?)",
        ("SBER", "BBG001", "share", "Sber", "RUB", 1),
    )
    # Pre-populate metadata so decide_strategy returns "skip".
    # Use today's date so the test is robust to the wall clock.
    today = date.today()
    con.execute(
        "INSERT INTO instrument_metadata (figi, last_bar_ts, total_bars, last_run_status) "
        "VALUES (?, ?, 252, 'ok')",
        ("BBG001", today.isoformat()),
    )
    con.commit()
    con.close()


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "src" / "algotrader_api" / "db" / "migrations"


class _OKUniverseOKCandles:
    async def get_shares(self):
        return [{"figi": "BBG001", "ticker": "SBER", "class": "share", "name": "Sber"}]
    async def get_bonds(self): return []
    async def get_etfs(self): return []
    async def get_futures(self): return []
    async def get_options(self): return []

    async def get_candles(self, **kw):
        return []  # no new candles — but metadata marks it 'ok', so strategy='skip'
    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_run_emits_skipped_event_for_up_to_date_ticker(tmp_path):
    db = tmp_path / "state.db"
    # Apply migrations
    from algotrader_api.db.sqlite import run_migrations as _rm
    _rm(str(db), str(_migrations_dir()))
    _seed_instruments_and_metadata(db)

    async def _sink(ev):
        events.append(ev)
    events = []
    runner = BackfillRunner(
        client=_OKUniverseOKCandles(),
        db_path=str(db),
        bars_dir=str(tmp_path),
        event_sink=_sink,
    )
    runner.event_sink = lambda ev: _async_noop(ev, events)
    await runner.run(history_years=1, incremental_threshold_days=2)
    # Skip branch fires → ticker_progress event with status='skipped'.
    skipped = [e for e in events if e.type == "ticker_progress" and e.payload.get("status") == "skipped"]
    assert len(skipped) >= 1


@pytest.mark.asyncio
async def test_run_emits_ticker_error_event_when_get_candles_raises(tmp_path):
    """get_candles() raises → _backfill_one catches and emits ticker_progress error event."""
    db = tmp_path / "state.db"
    from algotrader_api.db.sqlite import run_migrations as _rm
    _rm(str(db), str(_migrations_dir()))

    # Seed only instruments (no metadata) so decide_strategy picks 'full'.
    con = sqlite3.connect(str(db))
    con.execute(
        """CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE,
            class TEXT, name TEXT,
            currency TEXT, lot_size INTEGER, isin TEXT, sector TEXT,
            source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) VALUES (?, ?, ?, ?, ?, ?)",
        ("SBER", "BBG001", "share", "Sber", "RUB", 1),
    )
    con.commit()
    con.close()

    class _BoomGetCandles:
        async def get_shares(self):
            return [{"figi": "BBG001", "ticker": "SBER", "class": "share", "name": "Sber"}]
        async def get_bonds(self): return []
        async def get_etfs(self): return []
        async def get_futures(self): return []
        async def get_options(self): return []
        async def get_candles(self, **kw):
            raise ConnectionError("upstream down")
        async def aclose(self):
            pass

    events = []
    runner = BackfillRunner(
        client=_BoomGetCandles(),
        db_path=str(db),
        bars_dir=str(tmp_path),
        event_sink=lambda ev: _async_noop(ev, events),
    )
    await runner.run(history_years=1, incremental_threshold_days=2)
    error_events = [
        e for e in events
        if e.type == "ticker_progress" and e.payload.get("status") == "error"
    ]
    assert error_events
    assert "upstream down" in error_events[0].payload.get("error", "")


@pytest.mark.asyncio
async def test_run_breaks_loop_when_stop_called_mid_run(tmp_path):
    """stop() called inside an event_sink callback triggers loop exit.

    Existing test_stop_signal_causes_runner_to_exit_between_tickers in
    test_backfill.py covers the same path through public stop(); this
    test pins the contract that mid-run stop() flips final_state to IDLE
    (not DONE).
    """
    db = tmp_path / "state.db"
    from algotrader_api.db.sqlite import run_migrations as _rm
    _rm(str(db), str(_migrations_dir()))

    con = sqlite3.connect(str(db))
    con.execute(
        """CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE,
            class TEXT, name TEXT,
            currency TEXT, lot_size INTEGER, isin TEXT, sector TEXT,
            source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    for i in range(3):
        con.execute(
            "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) VALUES (?, ?, ?, ?, ?, ?)",
            (f"T{i}", f"BBG00{i}", "share", f"T{i}", "RUB", 1),
        )
    con.commit()
    con.close()

    state_at_stop = []

    async def _sink(ev):
        # After first ticker event, signal stop.
        if ev.type == "ticker_progress":
            runner.stop()
            state_at_stop.append(ev.payload.get("status"))

    runner = BackfillRunner(
        client=_OKUniverseOKCandles(),
        db_path=str(db),
        bars_dir=str(tmp_path),
        event_sink=_sink,
    )
    runner.event_sink = _sink
    await runner.run(history_years=1, incremental_threshold_days=2)
    # stop() mid-run flips final state to IDLE.
    assert runner.state.value == "idle"
    # The first ticker event was observed.
    assert len(state_at_stop) >= 1


async def _async_noop(ev, sink):
    sink.append(ev)


def test_emit_swallows_sink_exceptions(tmp_path):
    """_emit must not break the run when the event_sink raises."""
    async def noop_sink(ev):
        return None
    runner = BackfillRunner(
        client=_OKUniverseOKCandles(),
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path),
        event_sink=noop_sink,
    )

    async def bad_sink(ev):
        raise RuntimeError("sink exploded")

    runner.event_sink = bad_sink
    # Should not raise even though sink is broken.
    import asyncio
    asyncio.run(runner._emit("done", {"status": "ok"}))


def test_candle_date_handles_legacy_nested_time_dict(tmp_path):
    """_backfill_one._candle_date with legacy dict shape (year/month/day)."""
    from datetime import date, timedelta

    db = tmp_path / "state.db"
    from algotrader_api.db.sqlite import run_migrations as _rm
    _rm(str(db), str(_migrations_dir()))

    async def noop_sink(ev):
        return None
    runner = BackfillRunner(
        client=_OKUniverseOKCandles(),
        db_path=str(db),
        bars_dir=str(tmp_path),
        event_sink=noop_sink,
    )
    yesterday = date.today() - timedelta(days=1)

    class _LegacyTimeClient:
        async def get_shares(self): return []
        async def get_bonds(self): return []
        async def get_etfs(self): return []
        async def get_futures(self): return []
        async def get_options(self): return []
        async def get_candles(self, **kw):
            return [
                {"time": {"year": yesterday.year, "month": yesterday.month,
                          "day": yesterday.day},
                 "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
            ]
        async def aclose(self): pass

    runner.client = _LegacyTimeClient()
    inner = runner._backfill_one
    import asyncio
    asyncio.run(inner(figi="BBG001", from_=yesterday, to=yesterday))


def test_candle_date_handles_invalid_date_string(tmp_path):
    """ts='not a date' → _candle_dict fast-path returns None → filtered out."""
    from datetime import date, timedelta

    db = tmp_path / "state.db"
    from algotrader_api.db.sqlite import run_migrations as _rm
    _rm(str(db), str(_migrations_dir()))

    async def noop_sink(ev):
        return None
    runner = BackfillRunner(
        client=_OKUniverseOKCandles(),
        db_path=str(db),
        bars_dir=str(tmp_path),
        event_sink=noop_sink,
    )
    yesterday = date.today() - timedelta(days=1)
    # Run a full _backfill_one with a candle that has an invalid ts
    # (fast-path returns None → candle is filtered out → no crash).
    class _BadTsClient:
        async def get_shares(self): return []
        async def get_bonds(self): return []
        async def get_etfs(self): return []
        async def get_futures(self): return []
        async def get_options(self): return []
        async def get_candles(self, **kw):
            return [{"ts": "not a date", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        async def aclose(self): pass

    runner.client = _BadTsClient()
    import asyncio
    # Should not raise.
    asyncio.run(runner._backfill_one(figi="BBG001", from_=yesterday, to=yesterday))
