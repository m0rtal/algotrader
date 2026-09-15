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
        event_sink=_sink,
    )
    events = []
    runner.event_sink = lambda ev: _async_noop(ev, events)
    await runner.run(history_years=1, incremental_threshold_days=2)
    done_events = [e for e in events if e.type == "done"]
    assert done_events
    assert done_events[0].payload["status"] == "ok"

@pytest.mark.asyncio
async def test_run_processes_figis_in_parallel(tmp_path):
    """BackfillRunner should process multiple figis concurrently.

    With concurrency, total wall time for N figis × sleep(S) should be
    much less than N × S. If backfill is purely sequential the test
    fails the timing assertion.
    """
    import time

    db = tmp_path / "state.db"
    _seed_migrations(db, _migrations_dir())
    _seed_instruments(db)
    # Add more instruments for the parallelism to matter
    con = sqlite3.connect(str(db))
    for i in range(3, 11):  # BBG003..BBG010 = 10 figis total (SBER, GAZP + 8)
        con.execute(
            "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) VALUES (?, ?, ?, ?, ?, ?)",
            (f"T{i}", f"BBG{i:03d}", "share", f"T{i}", "RUB", 1),
        )
    con.commit()
    con.close()

    sleep_per_call = 0.5  # seconds

    class _SlowClient:
        async def get_shares(self):
            return [{"figi": "BBG001", "ticker": "SBER", "class": "share", "name": "Sber"}]

        async def get_bonds(self): return []
        async def get_etfs(self): return []
        async def get_futures(self): return []
        async def get_options(self): return []

        async def get_candles(self, **kw):
            await asyncio.sleep(sleep_per_call)
            return [{"ts": (date.today() - timedelta(days=1)).isoformat(),
                     "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

        async def aclose(self):
            pass

    async def _sink(ev):
        pass

    runner = BackfillRunner(client=_SlowClient(), db_path=str(db), event_sink=_sink)

    start = time.time()
    await runner.run(history_years=0, incremental_threshold_days=1)
    elapsed = time.time() - start

    # With concurrency (semaphore=10), 10 figis * 0.5s should finish in well
    # under 5s (vs 5s+ sequential). Generous bound.
    assert elapsed < 4.0, (
        f"backfill took {elapsed:.1f}s for 10 figis × 0.5s each — "
        f"appears sequential. Expected <4s with concurrency."
    )

@pytest.mark.asyncio
async def test_backfill_one_empty_response_marks_metadata_as_up_to_date(tmp_path):
    """When broker returns 0 candles (e.g., delisted figi), _backfill_one
    must mark metadata.last_bar_ts to today so decide_strategy skips
    the figi on the next run instead of looping forever.
    """
    db = tmp_path / "state.db"
    _seed_migrations(db, _migrations_dir())
    _seed_instruments(db)

    from datetime import date

    class _EmptyClient:
        async def get_candles(self, **kw):
            return []
        async def aclose(self):
            pass

    async def _sink(ev):
        pass

    runner = BackfillRunner(client=_EmptyClient(), db_path=str(db), event_sink=_sink)
    today = date.today()
    await runner._backfill_one(
        figi="BBG001", ticker="SBER", from_=today, to=today,
    )

    # metadata should have last_bar_ts=today (so next run skips via decide_strategy)
    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT last_bar_ts, last_run_status FROM instrument_metadata WHERE figi='BBG001'"
    ).fetchone()
    con.close()
    assert row is not None, "metadata should be created"
    assert row[1] == "skipped", f"status should be 'skipped', got {row[1]!r}"
    # last_bar_ts must be set to today (or close to it) so next run sees
    # days_since < incremental_threshold_days and skips
    assert row[0] is not None, (
        "last_bar_ts should NOT be None — that would cause decide_strategy "
        "to return 'full' and trigger an infinite retry loop"
    )
    last_ts = date.fromisoformat(row[0])
    assert last_ts == today, f"expected {today}, got {last_ts}"


@pytest.mark.asyncio
async def test_decide_strategy_skips_figi_with_fresh_last_bar_ts():
    """If last_bar_ts = today, decide_strategy should return 'skip'."""
    from algotrader_api.ingestion.backfill import decide_strategy
    from datetime import date

    today = date.today()
    row = {"last_bar_ts": today.isoformat()}
    strategy, from_, to = decide_strategy(
        metadata_row=row, today=today, history_years=5, incremental_threshold_days=1,
    )
    assert strategy == "skip"
    assert from_ is None
    assert to is None
