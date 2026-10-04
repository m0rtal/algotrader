"""Catch reconciliation commit outside rollback protection and BUSY deferral.

SQL, cached connections and kernel flock are real, on migrated temporary DBs.
Only commit failures are injected: WAL commit BUSY is not deterministic.
"""
from contextlib import contextmanager
import fcntl
import os
import sqlite3

import pytest

from algotrader_api.db import bars_sqlite, sqlite as sqlitedb
from algotrader_api.ingestion import no_trade_evidence as evidence
from algotrader_api.ingestion import writer_lock as locks


class Interrupted(BaseException):
    pass


def failure_for(kind):
    if kind == "interrupt":
        return Interrupted("interrupted")
    exc = sqlite3.OperationalError("private-token-do-not-log database is locked")
    if kind != "missing":
        exc.sqlite_errorcode = {"busy": 5, "snapshot": 517, "other": 14, "locked": 6}[kind]
    return exc


def kernel_lock_held(db):
    fd = os.open(locks.writer_lock_path(db), os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


class CommitFailure:
    def __init__(self, conn, db, failure, *, fail_at: int | None = 1, rollback_failure=None):
        self.conn = conn
        self.db = db
        self.failure = failure
        self.fail_at = fail_at
        self.rollback_failure = rollback_failure
        self.commits = 0
        self.failed_commit_state = []
        self.rollback_locks = []
        self.closes = 0

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def commit(self):
        self.commits += 1
        if self.commits == self.fail_at:
            self.failed_commit_state.append((
                self.conn.in_transaction,
                self.conn.execute("SELECT COUNT(*) FROM moex_no_trade_evidence").fetchone()[0],
                kernel_lock_held(self.db),
            ))
            raise self.failure
        return self.conn.commit()

    def rollback(self):
        # Record rather than assert here: the wrapper preserves the original
        # error even if rollback raises, so an assertion here could be swallowed.
        self.rollback_locks.append(kernel_lock_held(self.db))
        if self.rollback_failure is not None:
            raise self.rollback_failure
        return self.conn.rollback()

    def close(self):
        self.closes += 1
        return self.conn.close()


def seed(db, *, real_bar=True):
    conn = sqlitedb.get_connection(db)
    conn.execute("INSERT INTO instruments(figi,ticker,class,name,currency,lot_size) "
                 "VALUES ('F','NT1','share','NT1','RUB',1)")
    conn.execute("INSERT INTO instrument_metadata(figi) VALUES ('F')")
    conn.executemany(
        "INSERT INTO moex_no_trade_evidence "
        "(figi,session_date,board,isin,observed_at,expires_at) "
        "VALUES ('F',?,'TQBR','RU','2026-09-30','2027-09-30')",
        [("2026-09-01",), ("2026-09-02",)],
    )
    if real_bar:
        conn.execute("INSERT INTO bars(figi,ts,open,high,low,close,volume,source) "
                     "VALUES ('F','2026-09-01',1,2,1,2,10,'moex')")
    conn.commit()
    return conn


def committed_rows(db):
    observer = sqlite3.connect(db, timeout=0)
    try:
        return (
            observer.execute("SELECT session_date FROM moex_no_trade_evidence ORDER BY session_date").fetchall(),
            observer.execute("SELECT ts,close,source FROM bars ORDER BY ts").fetchall(),
        )
    finally:
        observer.close()


def observe_reconcile_unlock(monkeypatch, conn, exits):
    real_lock = evidence._evidence_writer_lock

    @contextmanager
    def observe(db_path, **kwargs):
        with real_lock(db_path, **kwargs):
            try:
                yield
            finally:
                exits.append(conn.in_transaction)

    monkeypatch.setattr(evidence, "_evidence_writer_lock", observe)


@pytest.mark.parametrize("kind", ["busy", "snapshot", "other", "locked", "missing", "interrupt"])
def test_reconcile_commit_failure_rolls_back_before_unlock_and_reuses_cache(fresh_db, monkeypatch, kind):
    # Moving commit out of the protected try leaks the real DELETE transaction.
    conn = seed(fresh_db)
    before = committed_rows(fresh_db)
    failure = failure_for(kind)
    proxy = CommitFailure(conn, fresh_db, failure)
    exits = []
    observe_reconcile_unlock(monkeypatch, conn, exits)
    busy = kind in {"busy", "snapshot"}
    with pytest.raises(locks.WriterLockBusy if busy else type(failure)) as raised:
        evidence.reconcile_no_trade_evidence(proxy, db_path=fresh_db)
    if busy:
        assert raised.value.__cause__ is failure
        assert (raised.value.role, raised.value.phase, raised.value.reason) == (
            "evidence-reconcile", "reconcile", "sqlite-busy",
        )
        assert raised.value.database_path == fresh_db
        assert raised.value.lock_path == str(locks.writer_lock_path(fresh_db))
        assert raised.value.timeout_seconds == evidence._EVIDENCE_LOCK_TIMEOUT_SECONDS
        assert raised.value.result == "deferred"
    else:
        assert raised.value is failure
    assert proxy.failed_commit_state == [(True, 1, True)]  # Real DELETE ran under flock.
    assert proxy.rollback_locks == [True]
    assert exits == [False]
    assert not kernel_lock_held(fresh_db)
    assert not conn.in_transaction and proxy.closes == 0
    assert committed_rows(fresh_db) == before
    assert sqlitedb.get_connection(fresh_db) is conn
    assert evidence.reconcile_no_trade_evidence(conn, db_path=fresh_db) == 1
    assert not conn.in_transaction
    assert committed_rows(fresh_db) == ([("2026-09-02",)], [("2026-09-01", 2.0, "moex")])


def test_reconcile_actual_sqlite_busy_rolls_back_and_defers(fresh_db, monkeypatch):
    # Omitting numeric BUSY conversion incorrectly exposes OperationalError.
    conn = seed(fresh_db)
    conn.execute("PRAGMA busy_timeout=0")
    before = committed_rows(fresh_db)
    proxy = CommitFailure(conn, fresh_db, None, fail_at=None)
    exits = []
    observe_reconcile_unlock(monkeypatch, conn, exits)
    holder = sqlite3.connect(fresh_db, timeout=0)
    try:
        holder.execute("BEGIN IMMEDIATE")  # Independent SQLite writer, no flock.
        holder.execute("UPDATE instruments SET name='held' WHERE figi='F'")
        with pytest.raises(locks.WriterLockBusy) as raised:
            evidence.reconcile_no_trade_evidence(proxy, db_path=fresh_db)
        assert raised.value.reason == "sqlite-busy"
        assert raised.value.__cause__.sqlite_errorcode == 5
        assert proxy.rollback_locks == [True] and exits == [False]
        assert not conn.in_transaction and proxy.closes == 0
        assert not kernel_lock_held(fresh_db)
        assert committed_rows(fresh_db) == before
    finally:
        holder.rollback()
        holder.close()
    assert sqlitedb.get_connection(fresh_db) is conn
    assert evidence.reconcile_no_trade_evidence(conn, db_path=fresh_db) == 1


@pytest.mark.parametrize("kind", ["busy", "other"])
def test_reconcile_rollback_failure_preserves_original_and_releases_lock(fresh_db, kind):
    # Replacing the original exception with a rollback failure loses its meaning.
    conn = seed(fresh_db)
    before = committed_rows(fresh_db)
    failure = failure_for(kind)
    proxy = CommitFailure(conn, fresh_db, failure, rollback_failure=Interrupted("rollback failed"))
    with pytest.raises(locks.WriterLockBusy if kind == "busy" else type(failure)) as raised:
        evidence.reconcile_no_trade_evidence(proxy, db_path=fresh_db)
    assert (raised.value.__cause__ if kind == "busy" else raised.value) is failure
    assert proxy.rollback_locks == [True]
    assert not kernel_lock_held(fresh_db) and proxy.closes == 0
    assert committed_rows(fresh_db) == before
    # An unsuccessful rollback cannot guarantee transaction cleanup. The owner
    # performs cleanup here; production keeps PR #186's original-error policy.
    conn.rollback()
    assert not conn.in_transaction
    assert conn.execute("SELECT 1").fetchone()[0] == 1


@pytest.mark.parametrize("writer", ["replace_bars_for_figi", "replace_bars_for_figi_with_rowcount"])
@pytest.mark.parametrize("kind", ["busy", "snapshot", "other"])
def test_bar_commit_survives_real_reconcile_commit_failure(fresh_db, monkeypatch, capsys, writer, kind):
    # A swallowed hook commit failure must not leak a transaction into the cache.
    conn = seed(fresh_db, real_bar=False)
    proxy = CommitFailure(conn, fresh_db, failure_for(kind), fail_at=2)
    monkeypatch.setattr(bars_sqlite, "get_connection", lambda path: proxy if path == fresh_db else pytest.fail("wrong DB"))
    from algotrader_api import ui_snapshot
    monkeypatch.setattr(ui_snapshot, "maybe_refresh", lambda **kwargs: None)
    exits = []
    observe_reconcile_unlock(monkeypatch, conn, exits)
    candle = {"ts": "2026-09-01", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}
    assert getattr(bars_sqlite, writer)(fresh_db, "F", [candle], source="moex") == 1
    assert proxy.failed_commit_state == [(True, 1, True)]
    assert proxy.rollback_locks == [True] and exits == [False]
    assert not conn.in_transaction and proxy.closes == 0
    assert not kernel_lock_held(fresh_db)
    assert committed_rows(fresh_db) == ([("2026-09-01",), ("2026-09-02",)], [("2026-09-01", 2.0, "moex")])
    assert conn.execute("SELECT total_bars,last_run_status FROM instrument_metadata WHERE figi='F'").fetchone()[:] == (1, "ok")
    output = capsys.readouterr()
    lines = (output.out + output.err).splitlines()
    if kind in {"busy", "snapshot"}:
        assert len(lines) == 1
        for field in ("DEFER writer-lock-busy", "role=evidence-reconcile", "phase=reconcile",
                      "pid=", "database_path=", "lock_path=", "timeout=", "reason=sqlite-busy", "result=deferred"):
            assert field in lines[0]
        assert len(lines[0]) <= 2048
        assert "private-token" not in lines[0]
    else:
        assert lines == []  # Existing best-effort hook policy remains unchanged.
    assert sqlitedb.get_connection(fresh_db) is conn
    assert getattr(bars_sqlite, writer)(fresh_db, "F", [candle], source="moex") == 1
    assert not conn.in_transaction
    assert committed_rows(fresh_db) == ([("2026-09-02",)], [("2026-09-01", 2.0, "moex")])
