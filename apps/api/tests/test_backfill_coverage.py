"""Coverage tests for BackfillRunner.run() error paths and helpers."""

import asyncio
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from algotrader_api.ingestion import backfill as bf_mod
from algotrader_api.ingestion.backfill import BackfillRunner


def _seed_instruments(db_path: Path) -> None:
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
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) VALUES (?, ?, ?, ?, ?, ?)",
        ("SBER", "BBG001", "share", "Sber", "RUB", 1),
    )
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) VALUES (?, ?, ?, ?, ?, ?)",
        ("GAZP", "BBG002", "share", "Gazp", "RUB", 1),
    )
    con.commit()
    con.close()


def _seed_migrations(db_path: Path, migrations_dir: Path) -> None:
    from algotrader_api.db import sqlite as sqlitedb

    sqlitedb.run_migrations(str(db_path), str(migrations_dir))


class _BoomClient:
    """All SDK methods raise — exercises the unhandled-error paths."""

    async def get_shares(self):
        raise RuntimeError("boom shares")

    async def get_bonds(self):
        raise RuntimeError("boom bonds")

    async def get_etfs(self):
        raise RuntimeError("boom etfs")

    async def get_futures(self):
        raise RuntimeError("boom futures")

    async def get_options(self):
        raise RuntimeError("boom options")

    async def get_candles(self, **kw):
        raise RuntimeError("boom candles")

    async def aclose(self):
        pass


class _AlwaysBoomCandles:
    async def get_shares(self):
        return [{"figi": "BBG001", "ticker": "SBER", "class": "share", "name": "Sber"}]

    async def get_bonds(self):
        return []

    async def get_etfs(self):
        return []

    async def get_futures(self):
        return []

    async def get_options(self):
        return []

    async def get_candles(self, **kw):
        raise RuntimeError("boom candles")

    async def aclose(self):
        pass


def _migrations_dir() -> Path:
    # tests/test_backfill_coverage.py is at apps/api/tests/, so parent.parent = apps/api.
    return Path(__file__).resolve().parent.parent / "src" / "algotrader_api" / "db" / "migrations"


@pytest.mark.asyncio
async def test_run_universe_discovery_failure_emits_done_event(tmp_path):
    db = tmp_path / "state.db"
    _seed_migrations(db, _migrations_dir())

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_BoomClient(),
        db_path=str(db),
        bars_dir=str(tmp_path),
        event_sink=_sink,
    )
    events = []
    runner.event_sink = lambda ev: _async_noop(ev, events)
    await runner.run(history_years=1, incremental_threshold_days=2)
    # Universe discovery raised on every SDK method → runner still
    # emits at least one 'done' event and tickers_done is 0.
    done_events = [e for e in events if e.type == "done"]
    assert len(done_events) >= 1
    assert runner.tickers_done == 0


@pytest.mark.asyncio
async def test_run_per_ticker_error_logs_and_continues(tmp_path):
    db = tmp_path / "state.db"
    _seed_migrations(db, _migrations_dir())
    _seed_instruments(db)

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_AlwaysBoomCandles(),
        db_path=str(db),
        bars_dir=str(tmp_path),
        event_sink=_sink,
    )
    events = []
    runner.event_sink = lambda ev: _async_noop(ev, events)
    await runner.run(history_years=1, incremental_threshold_days=2)
    # Both tickers failed → both logged as 'unhandled' errors, runner
    # completed with state DONE.
    assert runner.state == bf_mod.BackfillState.DONE
    assert runner.tickers_done == 2


async def _async_noop(ev, events):
    events.append(ev)


def test_extract_last_bar_ts_handles_legacy_dict_with_ts_field(tmp_path):
    """_extract_last_bar_ts handles candles where ts is dict-shape."""
    from algotrader_api.ingestion.backfill import BackfillRunner

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_BoomClient(),
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path),
        event_sink=_sink,
    )
    yesterday = date.today() - timedelta(days=1)
    candles = [
        {"time": {"year": yesterday.year, "month": yesterday.month,
                  "day": yesterday.day}},
    ]
    out = runner._extract_last_bar_ts(candles)
    assert out == yesterday.isoformat()


def test_extract_last_bar_ts_skips_malformed_candles(tmp_path):
    from algotrader_api.ingestion.backfill import BackfillRunner

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_BoomClient(),
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path),
        event_sink=_sink,
    )
    # No time field → skipped.
    out = runner._extract_last_bar_ts([{"foo": "bar"}, {"also": "no time"}])
    assert out is None


def test_extract_last_bar_ts_skips_when_all_candles_fail(tmp_path):
    """All candles raise AttributeError → returns None."""
    from algotrader_api.ingestion.backfill import BackfillRunner

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_BoomClient(),
        db_path=str(tmp_path / "state.db"),
        bars_dir=str(tmp_path),
        event_sink=_sink,
    )

    class _Broken:
        time = "not an object"

    out = runner._extract_last_bar_ts([_Broken()])
    assert out is None


@pytest.mark.asyncio
async def test_run_emits_done_event_with_status_ok(tmp_path):
    db = tmp_path / "state.db"
    _seed_migrations(db, _migrations_dir())
    _seed_instruments(db)

    class _OKClient:
        async def get_shares(self):
            return [{"figi": "BBG001", "ticker": "SBER", "class": "share", "name": "Sber"}]
        async def get_bonds(self): return []
        async def get_etfs(self): return []
        async def get_futures(self): return []
        async def get_options(self): return []
        async def get_candles(self, **kw):
            return [{"ts": (date.today() - timedelta(days=1)).isoformat(),
                     "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        async def aclose(self):
            pass

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_OKClient(),
        db_path=str(db),
        bars_dir=str(tmp_path),
        event_sink=_sink,
    )
    events = []
    runner.event_sink = lambda ev: _async_noop(ev, events)
    await runner.run(history_years=1, incremental_threshold_days=2)
    done_events = [e for e in events if e.type == "done"]
    assert done_events
    assert done_events[0].payload["status"] == "ok"
