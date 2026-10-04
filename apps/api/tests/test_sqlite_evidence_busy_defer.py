"""Catch missing BUSY translation and rollback after commit/unlock.

Real file-backed SQLite and flock; HTTP helpers only are replaced.
Commit failures use a Connection subclass because SQLite WAL commit BUSY
is not deterministic. SQL contention uses an independent BEGIN IMMEDIATE.
"""
from contextlib import contextmanager
from datetime import date
import fcntl
import os
import sqlite3
import sys

import pytest

from algotrader_api.ingestion import no_trade_evidence as evidence
from algotrader_api.ingestion import writer_lock as locks
from test_aux_writer_lock_outcomes import _load_script, _migrate, _seed, _counters


class Interrupted(BaseException):
    pass


def error_for(kind):
    if kind == "interrupt":
        return Interrupted("interrupted")
    exc = sqlite3.OperationalError("private-token-do-not-log database is locked")
    if kind != "missing":
        exc.sqlite_errorcode = {"busy": 5, "snapshot": 517, "other": 1}[kind]
    return exc


@pytest.fixture
def database(tmp_path):
    db = tmp_path / "state.db"
    _migrate(db)
    _seed(db, "BBG00NT000A1", "NT1", "RU000A0NT0A1", bar_iso="2026-08-15")
    return db


def tracked_connection(db, failure=None):
    class Tracked(sqlite3.Connection):
        rolled_back = False
        closed = False

        def commit(self):
            if failure is not None:
                raise failure
            return super().commit()

        def rollback(self):
            # Separate open-file description proves kernel flock still held.
            fd = os.open(locks.writer_lock_path(db), os.O_RDWR)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            self.rolled_back = True
            return super().rollback()

        def close(self):
            self.closed = True
            return super().close()

    return sqlite3.connect(db, timeout=0.03, factory=Tracked)


def observed_lock(db, conn, exits):
    @contextmanager
    def acquire(path, *, role, phase, **kwargs):
        with locks.writer_lock(path, role=role, phase=phase, timeout_seconds=0.05):
            try:
                yield
            finally:
                exits.append(conn.in_transaction)
    return acquire


def assert_released(db):
    with locks.writer_lock(db, role="no-trade-evidence", phase="evidence", timeout_seconds=0.05):
        pass


@pytest.mark.parametrize("path", ["listed-till", "evidence"])
@pytest.mark.parametrize("kind", ["sql-busy", "busy", "snapshot", "other", "missing", "interrupt"])
def test_failed_write_rolls_back_before_unlock(database, monkeypatch, capsys, path, kind):
    db = database
    before = _counters(db)
    failure = None if kind == "sql-busy" else error_for(kind)
    conn = tracked_connection(db, failure)
    exits = []
    holder = sqlite3.connect(db, timeout=0.03)
    if kind == "sql-busy":
        holder.execute("BEGIN IMMEDIATE")  # Deliberately does not hold flock.
    busy = kind in {"sql-busy", "busy", "snapshot"}
    try:
        if path == "listed-till":
            mod = _load_script("no_trade_evidence")
            monkeypatch.setattr(mod.sqlite3, "connect", lambda *a, **kw: conn)
            monkeypatch.setattr(mod, "writer_lock", observed_lock(db, conn, exits))
            monkeypatch.setattr(mod, "_last_trading_day", lambda *a: date(2026, 9, 15))
            monkeypatch.setattr(mod, "_get_meta_moex", lambda *a, **kw: None)
            monkeypatch.setattr(mod, "_probe_board_last", lambda *a: ("TQCB", "2026-09-10", "RU000A0NT0A1"))
            monkeypatch.setattr(mod, "_fetch_year_moex_outcome", lambda *a, **kw: pytest.fail("fetch after failed listed-till"))
            monkeypatch.setattr(evidence, "fetch_issuer_identity", lambda *a: {"isin": "RU000A0NT0A1"})
            monkeypatch.setattr(sys, "argv", [str(mod.__file__), "--db", str(db), "--days", "60", "--sleep", "0"])
            if busy:
                assert mod.main() == 75
                lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("DEFER")]
                assert len(lines) == 1
                assert "phase=listed-till" in lines[0]
                assert "reason=sqlite-busy" in lines[0]
                assert len(lines[0]) <= 2048
                assert "private-token" not in lines[0]
            else:
                with pytest.raises(type(failure)) as raised:
                    mod.main()
                assert raised.value is failure
            assert conn.closed
        else:
            monkeypatch.setattr(evidence, "_evidence_writer_lock", observed_lock(db, conn, exits))
            def write():
                return evidence.record_no_trade_evidence(
                    conn, db_path=str(db), figi="BBG00NT000A1",
                    rows=[{"ts": "2026-09-08"}, {"ts": "2026-09-09"}],
                    board="TQCB", isin="RU000A0NT0A1", now=date(2026, 9, 15),
                )
            if busy:
                with pytest.raises(locks.WriterLockBusy) as raised:
                    write()
                assert raised.value.reason == "sqlite-busy"
                assert raised.value.phase == "evidence"
                assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
                if failure is not None:
                    assert raised.value.__cause__ is failure
            else:
                with pytest.raises(type(failure)) as raised:
                    write()
                assert raised.value is failure
            assert not conn.closed
            assert conn.execute("SELECT COUNT(*) FROM moex_no_trade_evidence").fetchone()[0] == 0
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        assert conn.rolled_back
        assert exits == [False]
        assert_released(db)
    finally:
        holder.rollback()
        holder.close()
        if not conn.closed:
            conn.close()
        monkeypatch.undo()
    assert _counters(db) == before


def test_cli_evidence_sqlite_busy_defers(database, monkeypatch, capsys):
    db = database
    before = _counters(db)
    conn = tracked_connection(db)
    holder = sqlite3.connect(db, timeout=0.03)
    exits = []
    mod = _load_script("no_trade_evidence")
    monkeypatch.setattr(mod.sqlite3, "connect", lambda *a, **kw: conn)
    monkeypatch.setattr(mod, "_last_trading_day", lambda *a: date(2026, 9, 15))
    monkeypatch.setattr(mod, "_get_meta_moex", lambda *a, **kw: {"board": "TQCB", "market": "bonds"})
    monkeypatch.setattr(evidence, "fetch_issuer_identity", lambda *a: {"isin": "RU000A0NT0A1"})
    monkeypatch.setattr(evidence, "_evidence_writer_lock", observed_lock(db, conn, exits))
    calls = []

    def fetch(market, board, ticker, year, **kwargs):
        assert (market, board, ticker, year) == ("bonds", "TQCB", "NT1", 2026)
        calls.append(year)
        assert not conn.in_transaction
        holder.execute("BEGIN IMMEDIATE")
        return [{"ts": "2026-09-08", "_secid": "NT1", "_boardid": "TQCB",
                 "_numtrades": 0, "_value": 0, "volume": 0,
                 "open": None, "high": None, "low": None, "close": None}], "complete"

    monkeypatch.setattr(mod, "_fetch_year_moex_outcome", fetch)
    monkeypatch.setattr(sys, "argv", [str(mod.__file__), "--db", str(db), "--days", "60", "--sleep", "0"])
    try:
        assert mod.main() == 75
        assert calls == [2026]
        output = capsys.readouterr()
        lines = [line for line in output.out.splitlines() if line.startswith("DEFER")]
        assert len(lines) == 1
        assert "phase=evidence" in lines[0]
        assert "reason=sqlite-busy" in lines[0]
        assert len(lines[0]) <= 2048
        assert "private-token" not in output.out + output.err
        assert conn.rolled_back and conn.closed
        assert exits == [False]
        assert_released(db)
    finally:
        holder.rollback()
        holder.close()
        if not conn.closed:
            conn.close()
        monkeypatch.undo()
    assert _counters(db) == before


@pytest.mark.parametrize("code, expected", [(5, True), (517, True), (1, False), (6, False), (None, False)])
def test_numeric_busy_classification_only(code, expected):
    exc = sqlite3.OperationalError("database is locked")
    if code is not None:
        exc.sqlite_errorcode = code
    assert locks.is_sqlite_busy(exc) is expected
    assert locks.is_sqlite_busy(ValueError("database is locked")) is False
