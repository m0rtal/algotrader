from __future__ import annotations

import asyncio
import fcntl
import importlib
import socket
import sqlite3
import sys
from collections import deque
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests


class FixedDate(date):
    @classmethod
    def today(cls):
        # SQLite's built-in adapter handles date, not date subclasses.
        return date(2026, 9, 22)

    @classmethod
    def fromisoformat(cls, value):
        return date.fromisoformat(value)


def candle(figi, ts="2026-09-21"):
    return dict(figi=figi, ts=ts, open=10, high=10, low=10, close=10, volume=1)


class Broker:
    def __init__(self):
        self.answers = {}
        self.calls = []
        self.loops = []
        self.active = set()
        self.close_count = 0
        self.fixture_failures = []

    async def get_candles(self, **kwargs):
        task = asyncio.current_task()
        self.active.add(task)
        self.calls.append(kwargs)
        self.loops.append(asyncio.get_running_loop())
        try:
            await asyncio.sleep(0)
            if not self.answers.get(kwargs["figi"]):
                self.fixture_failures.append(kwargs)
                raise AssertionError("unexpected extra candle fetch")
            value = self.answers[kwargs["figi"]].popleft()
            if isinstance(value, BaseException):
                raise value
            return value
        finally:
            self.active.remove(task)

    async def aclose(self):
        assert not self.active
        self.loops.append(asyncio.get_running_loop())
        self.close_count += 1


@pytest.fixture
def offline(tmp_path, monkeypatch):
    # Guard before actual worker import. No Settings instance or broker factory runs.
    def forbidden(*args, **kwargs):
        raise AssertionError("network/settings/SDK/secrets forbidden in offline test")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    from algotrader_api import config
    from algotrader_api.ingestion import client as client_mod
    monkeypatch.setattr(config, "get_settings", forbidden)
    monkeypatch.setattr(config, "Settings", forbidden)
    monkeypatch.setattr(client_mod, "get_broker_token", forbidden)
    monkeypatch.setattr(client_mod, "load_broker_token", forbidden)
    monkeypatch.setattr(client_mod, "make_client", forbidden)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        sys.modules.pop("worker", None)
        worker = importlib.import_module("worker")
    finally:
        sys.path.pop(0)
    from algotrader_api.ingestion import backfill
    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.db.migrations import MIGRATIONS_DIR
    sqlitedb.close_all()
    db = str(tmp_path / "state.db")
    sqlitedb.run_migrations(db, MIGRATIONS_DIR)
    broker = Broker()
    events = []
    original_emit = backfill.BackfillRunner._emit

    async def record_emit(self, event_type, payload):
        events.append((event_type, dict(payload)))
        await original_emit(self, event_type, payload)

    monkeypatch.setattr(backfill.BackfillRunner, "_emit", record_emit)
    monkeypatch.setattr(worker, "date", FixedDate)
    monkeypatch.setattr(backfill, "date", FixedDate)
    monkeypatch.setattr(worker, "logger", MagicMock())
    monkeypatch.setattr(client_mod, "make_client", lambda **kwargs: broker)
    assert Path(worker.__file__).resolve() == Path(__file__).resolve().parents[1] / "worker.py"
    assert worker._step_gap_recovery.__code__.co_filename == worker.__file__
    env = SimpleNamespace(worker=worker, backfill=backfill, broker=broker,
                          db=db, events=events, monkeypatch=monkeypatch)
    try:
        yield env
    finally:
        sqlitedb.close_all()
        sys.modules.pop("worker", None)
        assert not broker.fixture_failures
        if broker.calls:
            assert all(not answers for answers in broker.answers.values())


def seed(env, figi, dates=("2026-09-18",), answers=([],)):
    with sqlite3.connect(env.db) as con:
        con.execute("INSERT INTO instruments(ticker,figi,class,name,currency,lot_size) "
                    "VALUES(?,?,'stock',?,'RUB',1)", (figi, figi, figi))
        con.executemany("INSERT INTO bars(figi,ts,open,high,low,close,volume) "
                        "VALUES(?,?,10,10,10,10,1)", [(figi, ts) for ts in dates])
    env.broker.answers[figi] = deque(answers)


def rows(env):
    with sqlite3.connect(env.db) as con:
        return con.execute("SELECT figi,ts FROM bars ORDER BY figi,ts").fetchall()


def assert_closed(env):
    assert env.broker.close_count == 1
    assert not env.broker.active
    assert len({id(loop) for loop in env.broker.loops}) == 1


def test_real_runner_all_chunks_failed(offline):
    e = offline
    seed(e, "A", answers=(RuntimeError("offline fetch failure"),))
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is False
    assert "failed=1" in detail
    assert ("ticker_progress", {"figi": "A", "status": "error", "bars_written": 0,
                                "error": "offline fetch failure"}) in e.events
    with sqlite3.connect(e.db) as con:
        assert con.execute("SELECT last_run_status FROM instrument_metadata "
                           "WHERE figi='A'").fetchone() == ("error",)
    assert sum(t == "ticker_progress" and p.get("status") == "error"
               for t, p in e.events) == 1
    assert_closed(e)


def test_real_partial_commit_and_continuation(offline):
    e = offline
    seed(e, "A", answers=([candle("A")],))
    seed(e, "B", answers=(RuntimeError("offline fetch failure"),))
    seed(e, "C", answers=([candle("C")],))
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is False
    assert "failed=1" in detail
    assert "2 bars filled" in detail
    assert "historical=0" in detail and "trailing=2" in detail
    assert "moex=0" in detail and "tinkoff=2" in detail
    assert ("A", "2026-09-21") in rows(e) and ("C", "2026-09-21") in rows(e)
    assert [call["figi"] for call in e.broker.calls] == ["A", "B", "C"]
    assert sum(t == "ticker_progress" and p.get("status") == "error"
               for t, p in e.events) == 1
    assert_closed(e)


def test_raised_ordinary_failures_count_unique_figis_and_continue(offline):
    e = offline
    for figi in ("A", "B", "C"):
        seed(e, figi)
    attempted = []

    async def boundary(self, *, figi, ticker, from_, to, source):
        attempted.append(figi)
        if figi in {"A", "B"}:
            raise RuntimeError("offline ordinary failure")
        return 0

    e.monkeypatch.setattr(e.backfill.BackfillRunner, "_backfill_one", boundary)
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is False and "failed=2" in detail
    assert attempted == ["A", "B", "C"]
    assert not detail.startswith("DEFER")
    assert_closed(e)


def test_exception_and_duplicate_events_count_once(offline):
    e = offline
    seed(e, "A")
    seed(e, "B")
    attempted = []

    async def boundary(self, *, figi, ticker, from_, to, source):
        attempted.append(figi)
        await self._emit("log", {"figi": figi, "status": "error"})
        await self._emit("ticker_progress", {"figi": "unrelated", "status": "error"})
        if figi == "A":
            for _ in range(2):
                await self._emit("ticker_progress", {"figi": figi, "status": "error"})
            raise RuntimeError("DO_NOT_COPY_RAW_PAYLOAD")
        return 0

    e.monkeypatch.setattr(e.backfill.BackfillRunner, "_backfill_one", boundary)
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is False and "failed=1" in detail
    assert attempted == ["A", "B"]
    assert "DO_NOT_COPY_RAW_PAYLOAD" not in detail
    failed = [call.kwargs for call in e.worker.logger.warning.call_args_list
              if call.args == ("worker.gap_recovery.fill_failed",)]
    assert failed == [{"figi": "A", "error_type": "RuntimeError"}]
    assert_closed(e)


@pytest.mark.parametrize("answers, reported", [(([],), 0), (([candle("A", "2026-09-18")],), 1)])
def test_real_zero_new_rows_not_failure(offline, answers, reported):
    e = offline
    seed(e, "A", answers=answers)
    before = rows(e)
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is True, (detail, e.worker.logger.warning.call_args_list)
    assert f"{reported} bars filled" in detail and "failed=0" in detail
    assert rows(e) == before
    assert not any(t == "ticker_progress" and p.get("status") == "error"
                   for t, p in e.events)
    assert_closed(e)


def test_no_gaps(offline):
    e = offline
    seed(e, "A", dates=("2026-09-22",), answers=())
    assert e.worker._step_gap_recovery(e.db) == (True, "gap recovery: no gaps")
    assert e.broker.calls == []
    assert_closed(e)


def test_historical_and_trailing_share_loop(offline):
    e = offline
    seed(e, "A", dates=("2026-09-16", "2026-09-18"),
         answers=([candle("A", "2026-09-17")], [candle("A")]))
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is True and "failed=0" in detail
    assert "2 bars filled" in detail and "historical=1 across 1 gaps" in detail
    assert "trailing=1 across 1 figis" in detail and "tinkoff=1" in detail
    assert ("A", "2026-09-17") in rows(e) and ("A", "2026-09-21") in rows(e)
    assert len(e.broker.calls) == 2
    assert_closed(e)


def test_historical_error_event_fails_phase_and_continues_trailing(offline):
    e = offline
    seed(e, "A", dates=("2026-09-16", "2026-09-18"),
         answers=(RuntimeError("offline historical failure"), []))
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is False and "failed=1" in detail
    assert len(e.broker.calls) == 2
    assert [call["figi"] for call in e.broker.calls] == ["A", "A"]
    assert any(t == "ticker_progress" and p.get("status") == "error" for t, p in e.events)
    assert_closed(e)


@pytest.mark.parametrize("metadata", [True, False])
def test_real_lock_contention(offline, metadata):
    e = offline
    from algotrader_api.ingestion import writer_lock as locks
    from algotrader_api.db import bars_sqlite
    seed(e, "A", answers=(RuntimeError("offline fetch failure") if metadata else [candle("A")],))

    @contextmanager
    def bounded_lock(path, **kwargs):
        kwargs["timeout_seconds"] = 0.01
        with locks.writer_lock(path, **kwargs):
            yield

    e.monkeypatch.setattr(e.backfill, "writer_lock", bounded_lock)
    e.monkeypatch.setattr(bars_sqlite, "writer_lock", bounded_lock)
    before = rows(e)
    with open(e.db + ".writer.lock", "a+b") as descriptor:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is False and rows(e) == before
    if metadata:
        assert detail.startswith("DEFER writer-lock-busy")
        assert "backfill-metadata" in detail and "result=deferred" in detail
        with sqlite3.connect(e.db) as con:
            assert con.execute("SELECT COUNT(*) FROM instrument_metadata").fetchone() == (0,)
    else:
        assert "failed=1" in detail and not detail.startswith("DEFER")
    assert_closed(e)


def test_direct_cancellation_propagates_after_settlement(offline):
    e = offline
    seed(e, "A", answers=(asyncio.CancelledError("offline cancellation"),))
    with pytest.raises(asyncio.CancelledError, match="offline cancellation"):
        e.worker._step_gap_recovery(e.db)
    assert_closed(e)


@pytest.mark.parametrize("phase, expected", [("gap_recovery", ["gap_recovery", "guardian"]),
                                             ("backfill_moex", ["backfill_moex"])])
def test_actual_chain_best_effort_and_critical(offline, phase, expected):
    e = offline
    seen = []
    statuses = []

    def fail(db):
        seen.append(phase)
        return False, "gap recovery: 0 bars filled (failed=1)"

    def guardian(db):
        seen.append("guardian")
        return True, "offline guardian"

    e.monkeypatch.setattr(e.worker, "get_settings",
                         lambda: SimpleNamespace(sqlite_path=e.db, log_level="INFO"))
    e.monkeypatch.setattr(e.worker, "setup_logging", lambda **kwargs: None)
    e.monkeypatch.setattr(e.worker, "heartbeat_loop", lambda *args: None)
    e.monkeypatch.setattr(e.worker, "_selected_phases", lambda subset: (phase, "guardian"))
    e.monkeypatch.setitem(e.worker._STEP_FUNCS, phase, fail)
    e.monkeypatch.setitem(e.worker._STEP_FUNCS, "guardian", guardian)
    e.monkeypatch.setattr(e.worker, "_log_chain_phase",
                         lambda db, phase, result, **kwargs: statuses.append((phase, result)))
    assert e.worker.run_daily_chain("daily") == 1
    assert seen == expected and statuses[0] == (phase, "error")
    assert e.worker._CRITICAL_PHASES == {"migrations", "universe_sync", "backfill_moex"}



def test_historical_and_two_source_counters(offline):
    e = offline
    seed(e, "A", dates=("2026-09-21",))
    seed(e, "S", dates=("2026-09-10", "2026-09-14"))
    calls = []

    async def boundary(self, *, figi, ticker, from_, to, source="auto"):
        calls.append((figi, source))
        if source == "auto":
            return 4
        return 3 if source == "moex" else 2

    e.monkeypatch.setattr(e.backfill.BackfillRunner, "_backfill_one", boundary)
    ok, detail = e.worker._step_gap_recovery(e.db)
    assert ok is True and "failed=0" in detail
    assert "9 bars filled" in detail and "historical=4 across 1 gaps" in detail
    assert "trailing=5 across 2 figis" in detail
    assert "moex=3" in detail and "tinkoff=2" in detail
    assert calls == [("S", "auto"), ("A", "tinkoff"), ("S", "moex")]
    assert_closed(e)
