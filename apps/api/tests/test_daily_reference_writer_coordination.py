"""Real daily transaction owners: order, contention, cleanup and retry state."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timezone
import asyncio
import importlib
import importlib.util
import fcntl
import sys
from types import SimpleNamespace
from pathlib import Path
import socket
import sqlite3
import threading
import time

import pytest

from algotrader_api.db import sqlite as sqlitedb
from algotrader_api.ingestion import backfill, universe
from algotrader_api.ingestion import writer_lock as locks
from algotrader_api.ingestion.universe_sync import run_universe_sync
from algotrader_api.data_quality import forward_adjustment
from algotrader_api.scripts_import import derive_splits
from algotrader_api.scripts_import import import_corporate_actions_common as corporate


@contextmanager
def observed_lock(db_path, *, role, phase, state, events, **kwargs):
    with locks.writer_lock(db_path, role=role, phase=phase, timeout_seconds=0.05):
        assert not state["held"]
        state["held"] = True
        events.append(("acquire", role, phase))
        try:
            yield
        finally:
            events.append(("release", role, phase))
            state["held"] = False


def connection_factory(real_connect, state, events, connections, failure=None,
                       rollback_failure=None, *, sqlite_timeout=None,
                       fail_operation=None, failure_at_commit=1,
                       failure_at_statement=1, sql_observer=None):
    class Tracked(sqlite3.Connection):
        _injected_rollback_failure = False
        _commits = 0
        _matching_statements = 0
        settings_at_begin = None

        def execute(self, sql, parameters=()):
            operation = sql.lstrip().split(None, 1)[0].upper()
            if sql_observer is not None:
                sql_observer(self, sql, parameters)
            if operation in {"BEGIN", "INSERT", "UPDATE", "DELETE", "REPLACE"}:
                assert state["held"], sql
                events.append((operation,))
            if operation == "BEGIN":
                self.settings_at_begin = tuple(
                    super(Tracked, self).execute(f"PRAGMA {name}").fetchone()[0]
                    for name in ("foreign_keys", "busy_timeout", "journal_mode")
                )
            if failure is not None and operation == fail_operation:
                self._matching_statements += 1
                if self._matching_statements == failure_at_statement:
                    raise failure
            return super().execute(sql, parameters)

        def commit(self):
            assert state["held"]
            events.append(("commit",))
            self._commits += 1
            if failure is not None and fail_operation is None and self._commits == failure_at_commit:
                raise failure
            return super().commit()

        def rollback(self):
            assert state["held"]
            events.append(("rollback",))
            if rollback_failure is not None:
                self._injected_rollback_failure = True
                raise rollback_failure
            return super().rollback()

        def close(self):
            try:
                assert not state["held"]
                if not self._injected_rollback_failure:
                    assert not self.in_transaction
            finally:
                super().close()
                events.append(("close",))

    def connect(database, *args, **kwargs):
        assert not state["held"], "connection opened inside owner lock"
        kwargs["factory"] = Tracked
        if sqlite_timeout is not None:
            kwargs["timeout"] = sqlite_timeout
        connection = real_connect(database, *args, **kwargs)
        connections.append(connection)
        return connection
    return connect


async def offline_sink(event):
    return None


class Interrupted(BaseException):
    pass


BROKER_ROW = {
    "ticker": "COORD", "figi": "FCOORD", "class": "share", "name": "Updated",
    "currency": "RUB", "lot_size": 10, "isin": "ISIN-NEW", "sector": "New sector",
}
FIXED_NOW = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)
OWNER_CASES = [
    pytest.param("universe", "universe-sync", "instruments", id="universe"),
    pytest.param("instrument", "backfill-metadata", "instruments", id="metadata-instrument"),
    pytest.param("seed", "backfill-metadata", "metadata", id="metadata-seed"),
    pytest.param("update", "backfill-metadata", "metadata", id="metadata-update"),
]


@pytest.fixture(autouse=True)
def deny_outbound_network(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent.parent))
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("unexpected-network")
        return real_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("unexpected-network")
        return real_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture
def coordinated_db(fresh_db):
    real_connect = sqlite3.connect
    con = real_connect(fresh_db)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            "INSERT INTO instruments "
            "(ticker, figi, class, name, currency, lot_size, isin, sector, "
            "expected_bars, source_updated_at, listed_till) "
            "VALUES ('COORD', 'FCOORD', 'share', 'Original', 'RUB', 1, "
            "'ISIN-OLD', 'Old sector', 123, '2020-01-02', '2024-05-31')"
        )
        con.execute(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
            "VALUES ('FCOORD', '2024-05-15', 50, 50, 50, 50, 100)"
        )
        con.commit()
    finally:
        con.close()
    sqlitedb.close_all()
    return fresh_db, real_connect


@pytest.fixture
def owner_trace(coordinated_db, monkeypatch):
    db, real_connect = coordinated_db
    state, events, connections = {"held": False}, [], []

    def install(**kwargs):
        real_sleep = time.sleep

        def sleep(seconds):
            assert not state["held"], "sleep inside owner lock"
            return real_sleep(seconds)

        monkeypatch.setattr(time, "sleep", sleep)

        def acquire(path, *, role, phase, **lock_kwargs):
            return observed_lock(path, role=role, phase=phase, state=state,
                                 events=events, **lock_kwargs)
        monkeypatch.setattr(universe, "writer_lock", acquire, raising=False)
        monkeypatch.setattr(backfill, "writer_lock", acquire, raising=False)
        monkeypatch.setattr(sqlite3, "connect", connection_factory(
            real_connect, state, events, connections, **kwargs,
        ))

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                # A different owner may legitimately hold flock during preparation.
                assert not state["held"]
                return FIXED_NOW

        monkeypatch.setattr(backfill, "datetime", FixedDatetime)

    yield state, events, connections, install
    # sqlite3 is shared by owner modules and cached helpers. Undo first.
    monkeypatch.undo()
    sqlitedb.close_all()


def snapshot(db, real_connect):
    con = real_connect(db)
    try:
        return {
            table: con.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            for table in ("instruments", "instrument_metadata", "bars", "ingestion_logs",
                          "corporate_actions", "bars_adjusted", "dividends")
        }
    finally:
        con.close()


def prepare_metadata_update(db, real_connect, owner):
    if owner != "update":
        return
    con = real_connect(db)
    try:
        con.execute(
            "INSERT INTO instrument_metadata "
            "(figi, first_bar_ts, last_bar_ts, total_bars, last_run_status, last_error, "
            "tinkoff_consecutive_failures, tinkoff_breaker_open) "
            "VALUES ('FCOORD', '2024-01-01', '2024-05-14', 5, 'error', 'old error', 2, 1)"
        )
        con.commit()
    finally:
        con.close()


def invoke_owner(owner, db, row=None, *, status="ok", error_msg=None):
    row = BROKER_ROW if row is None else row
    if owner == "universe":
        return universe.upsert_instruments(db, [row])
    runner = backfill.BackfillRunner(client=object(), db_path=db, event_sink=offline_sink)
    if owner == "instrument":
        return runner._upsert_instrument(row)
    if owner == "seed":
        return runner._seed_metadata_for_figi("FCOORD")
    assert owner == "update"
    return runner._upsert_metadata(figi="FCOORD", last_bar_ts="2024-05-15",
                                   total_bars=1, status=status, error_msg=error_msg)


def assert_saved(owner, db, real_connect):
    con = real_connect(db)
    try:
        if owner in {"universe", "instrument"}:
            assert con.execute(
                "SELECT ticker, figi, class, name, currency, lot_size, isin, sector, "
                "expected_bars, source_updated_at, listed_till FROM instruments"
            ).fetchall() == [("COORD", "FCOORD", "share", "Updated", "RUB", 10,
                              "ISIN-NEW", "New sector", 123, "2020-01-02", "2024-05-31")]
        elif owner == "seed":
            assert con.execute(
                "SELECT figi, last_bar_ts, total_bars, last_run_status, last_run_at, "
                "last_error, last_backfilled_at FROM instrument_metadata"
            ).fetchall() == [("FCOORD", None, 0, "pending", None, None, None)]
        else:
            assert con.execute(
                "SELECT figi, last_bar_ts, total_bars, last_run_status, last_run_at, "
                "last_error, last_backfilled_at, first_bar_ts, tinkoff_consecutive_failures, "
                "tinkoff_breaker_open FROM instrument_metadata"
            ).fetchall() == [("FCOORD", "2024-05-15", 1, "ok", FIXED_NOW.isoformat(),
                              None, FIXED_NOW.isoformat(), "2024-01-01", 2, 1)]
        assert con.execute("SELECT figi, ts, close FROM bars").fetchall() == [
            ("FCOORD", "2024-05-15", 50),
        ]
    finally:
        con.close()


def assert_closed(connections):
    assert connections
    for con in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            con.execute("SELECT 1")


def assert_unlocked(db, state):
    assert not state["held"]
    with open(locks.writer_lock_path(db), "a+b") as descriptor:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def assert_busy(exc, db, role, phase, *, reason, timeout):
    assert (exc.role, exc.phase, exc.reason, exc.result) == (role, phase, reason, "deferred")
    assert exc.database_path == str(locks.writer_lock_path(db))[:-len(".writer.lock")]
    assert exc.lock_path == str(locks.writer_lock_path(db))
    assert exc.timeout_seconds == timeout


@pytest.mark.parametrize("owner,role,phase", OWNER_CASES)
def test_universe_or_metadata_owner_transaction_order(coordinated_db, owner_trace, owner, role, phase):
    db, real_connect = coordinated_db
    state, events, connections, install = owner_trace
    prepare_metadata_update(db, real_connect, owner)
    install()
    assert invoke_owner(owner, db) == (1 if owner == "universe" else None)
    assert events == [("acquire", role, phase), ("BEGIN",), ("INSERT",),
                      ("commit",), ("release", role, phase), ("close",)]
    if owner == "universe":
        assert connections[0].settings_at_begin == (1, 30000, "wal")
    else:
        assert connections[0].settings_at_begin == (0, 5000, "wal")
    assert not state["held"]
    assert_saved(owner, db, real_connect)
    assert_closed(connections)


@pytest.mark.parametrize("owner,role,phase", OWNER_CASES)
def test_universe_or_metadata_flock_timeout_is_retryable(coordinated_db, owner_trace, owner, role, phase):
    db, real_connect = coordinated_db
    state, events, connections, install = owner_trace
    prepare_metadata_update(db, real_connect, owner)
    before = snapshot(db, real_connect)
    install()
    with open(locks.writer_lock_path(db), "a+b") as descriptor:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(locks.WriterLockBusy) as caught:
            invoke_owner(owner, db)
        assert_busy(caught.value, db, role, phase, reason="flock-timeout", timeout=0.05)
        assert events == [("close",)]
        assert snapshot(db, real_connect) == before
    assert_closed(connections)
    events.clear()
    invoke_owner(owner, db)
    assert events == [("acquire", role, phase), ("BEGIN",), ("INSERT",),
                      ("commit",), ("release", role, phase), ("close",)]
    assert_saved(owner, db, real_connect)
    assert_unlocked(db, state)


@pytest.mark.parametrize("owner,role,phase", OWNER_CASES)
@pytest.mark.parametrize("primary_type", [sqlite3.IntegrityError, Interrupted])
@pytest.mark.parametrize("broken_rollback", [False, True], ids=["healthy-rollback", "rollback-failure"])
def test_universe_or_metadata_commit_failure_preserves_primary(
    coordinated_db, owner_trace, owner, role, phase, primary_type, broken_rollback,
):
    db, real_connect = coordinated_db
    state, events, connections, install = owner_trace
    prepare_metadata_update(db, real_connect, owner)
    before = snapshot(db, real_connect)
    primary = primary_type("fixture-commit-failure")
    rollback_failure = sqlite3.OperationalError("fixture-rollback-failure") if broken_rollback else None
    install(failure=primary, rollback_failure=rollback_failure)
    with pytest.raises(primary_type) as caught:
        invoke_owner(owner, db)
    assert caught.value is primary
    assert events == [("acquire", role, phase), ("BEGIN",), ("INSERT",), ("commit",),
                      ("rollback",), ("release", role, phase), ("close",)]
    assert connections[0]._injected_rollback_failure is broken_rollback
    assert_closed(connections)
    assert snapshot(db, real_connect) == before
    assert_unlocked(db, state)
    install()
    invoke_owner(owner, db)
    assert_saved(owner, db, real_connect)
    assert_closed(connections)


@pytest.mark.parametrize("owner,role,phase", OWNER_CASES)
def test_universe_or_metadata_real_sqlite_busy_rolls_back_before_release(
    coordinated_db, owner_trace, owner, role, phase,
):
    db, real_connect = coordinated_db
    state, events, connections, install = owner_trace
    prepare_metadata_update(db, real_connect, owner)
    before = snapshot(db, real_connect)
    # Non-participating SQLite writer. Short wait exists only in this test factory.
    blocker = real_connect(db)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        install(sqlite_timeout=0.05)
        with pytest.raises(locks.WriterLockBusy) as caught:
            invoke_owner(owner, db)
        assert_busy(caught.value, db, role, phase, reason="sqlite-busy", timeout=30.0)
        assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
        assert caught.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_BUSY
        assert events == [("acquire", role, phase), ("BEGIN",), ("rollback",),
                          ("release", role, phase), ("close",)]
        assert snapshot(db, real_connect) == before
        assert_closed(connections)
        assert_unlocked(db, state)
    finally:
        blocker.rollback()
        blocker.close()
    invoke_owner(owner, db)
    assert_saved(owner, db, real_connect)


@pytest.mark.parametrize("owner,role,phase", OWNER_CASES)
@pytest.mark.parametrize("code", [5, 517, 1, 6, None], ids=["busy", "extended-busy", "error", "locked", "text-only"])
def test_universe_or_metadata_injected_commit_busy_is_numeric_only(
    coordinated_db, owner_trace, owner, role, phase, code,
):
    db, real_connect = coordinated_db
    state, events, connections, install = owner_trace
    prepare_metadata_update(db, real_connect, owner)
    before = snapshot(db, real_connect)
    primary = sqlite3.OperationalError("database is locked")
    if code is not None:
        primary.sqlite_errorcode = code
    install(failure=primary)
    translated = code in (5, 517)
    with pytest.raises(locks.WriterLockBusy if translated else sqlite3.OperationalError) as caught:
        invoke_owner(owner, db)
    if translated:
        assert_busy(caught.value, db, role, phase, reason="sqlite-busy", timeout=30.0)
        assert caught.value.__cause__ is primary
    else:
        assert caught.value is primary
    assert events == [("acquire", role, phase), ("BEGIN",), ("INSERT",), ("commit",),
                      ("rollback",), ("release", role, phase), ("close",)]
    assert snapshot(db, real_connect) == before
    assert_closed(connections)
    assert_unlocked(db, state)


@pytest.mark.parametrize("owner,role,phase", OWNER_CASES)
@pytest.mark.parametrize("operation", ["BEGIN", "INSERT"])
def test_universe_or_metadata_begin_and_body_errors_cleanup_under_lock(
    coordinated_db, owner_trace, owner, role, phase, operation,
):
    db, real_connect = coordinated_db
    state, events, connections, install = owner_trace
    prepare_metadata_update(db, real_connect, owner)
    before = snapshot(db, real_connect)
    primary = Interrupted("fixture-statement-interrupted")
    install(failure=primary, fail_operation=operation)
    with pytest.raises(Interrupted) as caught:
        invoke_owner(owner, db)
    assert caught.value is primary
    expected = [("acquire", role, phase), ("BEGIN",)]
    if operation == "INSERT":
        expected.append(("INSERT",))
    assert events == [*expected, ("rollback",), ("release", role, phase), ("close",)]
    assert snapshot(db, real_connect) == before
    assert_closed(connections)
    assert_unlocked(db, state)


@pytest.mark.parametrize("owner,role,phase", OWNER_CASES)
def test_universe_or_metadata_retry_is_idempotent(coordinated_db, owner_trace, owner, role, phase):
    db, real_connect = coordinated_db
    _, events, _, install = owner_trace
    prepare_metadata_update(db, real_connect, owner)
    install()
    result = invoke_owner(owner, db)
    first = snapshot(db, real_connect)
    assert invoke_owner(owner, db) == result
    assert snapshot(db, real_connect) == first
    assert events.count(("acquire", role, phase)) == 2
    assert_saved(owner, db, real_connect)


def test_universe_cached_transaction_is_not_committed_rolled_back_or_closed(coordinated_db, owner_trace):
    db, real_connect = coordinated_db
    state, events, connections, install = owner_trace
    cached = sqlitedb.get_connection(db)
    cached.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES ('MARKER', 'FCACHED-MARKER', 'share', 'uncommitted-marker', 'RUB', 1)"
    )
    before = snapshot(db, real_connect)
    install(sqlite_timeout=0.05)
    try:
        with pytest.raises(locks.WriterLockBusy) as caught:
            universe.upsert_instruments(db, [BROKER_ROW])
        assert_busy(caught.value, db, "universe-sync", "instruments", reason="sqlite-busy", timeout=30.0)
        assert caught.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_BUSY
        assert cached.in_transaction
        assert cached.execute(
            "SELECT name FROM instruments WHERE figi='FCACHED-MARKER'"
        ).fetchone()[0] == "uncommitted-marker"
        assert snapshot(db, real_connect) == before
        assert events == [("acquire", "universe-sync", "instruments"), ("BEGIN",), ("rollback",),
                          ("release", "universe-sync", "instruments"), ("close",)]
        assert all(con is not cached for con in connections)
        assert_closed(connections)
    finally:
        cached.rollback()
    assert universe.upsert_instruments(db, [BROKER_ROW]) == 1
    assert_saved("universe", db, real_connect)
    assert cached.execute("SELECT name FROM instruments").fetchone()[0] == "Updated"
    assert not cached.in_transaction
    assert_unlocked(db, state)


def test_universe_second_row_failure_keeps_first_commit_and_retries(coordinated_db, owner_trace):
    db, real_connect = coordinated_db
    _, events, connections, install = owner_trace
    rows = [BROKER_ROW, {**BROKER_ROW, "figi": "FCOORD-2"}, {**BROKER_ROW, "figi": "FCOORD-3"}]
    primary = sqlite3.IntegrityError("fixture-second-row-commit-failure")
    install(failure=primary, failure_at_commit=2)
    with pytest.raises(sqlite3.IntegrityError) as caught:
        universe.upsert_instruments(db, rows)
    assert caught.value is primary
    assert_saved("universe", db, real_connect)
    assert events == [
        ("acquire", "universe-sync", "instruments"), ("BEGIN",), ("INSERT",), ("commit",),
        ("release", "universe-sync", "instruments"),
        ("acquire", "universe-sync", "instruments"), ("BEGIN",), ("INSERT",), ("commit",),
        ("rollback",), ("release", "universe-sync", "instruments"), ("close",),
    ]
    assert_closed(connections)
    install()
    assert universe.upsert_instruments(db, rows) == 3
    first = snapshot(db, real_connect)
    assert universe.upsert_instruments(db, rows) == 3
    assert snapshot(db, real_connect) == first
    assert [row[1] for row in first["instruments"]] == ["FCOORD", "FCOORD-2", "FCOORD-3"]


def test_universe_slicing_keeps_101_separate_row_transactions(coordinated_db, owner_trace):
    db, real_connect = coordinated_db
    _, events, connections, install = owner_trace
    install()
    rows = [{**BROKER_ROW, "figi": f"FCOORD-{index:03d}"} for index in range(101)]
    assert universe.upsert_instruments(db, rows) == 101
    one_row = [("acquire", "universe-sync", "instruments"), ("BEGIN",), ("INSERT",),
               ("commit",), ("release", "universe-sync", "instruments")]
    assert events == one_row * 101 + [("close",)]
    assert len(snapshot(db, real_connect)["instruments"]) == 102
    assert len(connections) == 1
    assert_closed(connections)


def test_healthy_observer_requires_idle_but_always_native_closes(coordinated_db):
    db, real_connect = coordinated_db
    state, events, connections = {"held": False}, [], []
    con = connection_factory(real_connect, state, events, connections)(db)
    with observed_lock(db, role="universe-sync", phase="instruments", state=state, events=events):
        con.execute("BEGIN IMMEDIATE")
    with pytest.raises(AssertionError):
        con.close()
    assert not con._injected_rollback_failure
    assert_closed(connections)
    assert_unlocked(db, state)
    assert snapshot(db, real_connect)["bars"][0][5] == 50


@pytest.mark.parametrize("owner", ["universe", "instrument"], ids=["universe", "metadata-instrument"])
def test_universe_or_metadata_row_preparation_is_unlocked(coordinated_db, owner_trace, owner):
    db, real_connect = coordinated_db
    state, events, _, install = owner_trace

    class PreparedRow(dict):
        def __getitem__(self, key):
            assert_unlocked(db, state)
            return super().__getitem__(key)

        def get(self, key, default=None):
            assert_unlocked(db, state)
            return super().get(key, default)

    install()
    invoke_owner(owner, db, PreparedRow(BROKER_ROW))
    assert len([event for event in events if event[0] == "acquire"]) == 1
    assert_saved(owner, db, real_connect)


@pytest.mark.parametrize("use_runner", [False, True], ids=["universe-sync", "metadata-discovery"])
@pytest.mark.asyncio
async def test_universe_or_metadata_discovery_fetch_and_wait_are_unlocked(
    coordinated_db, monkeypatch, use_runner,
):
    import asyncio

    db, real_connect = coordinated_db
    state, events, fetched = {"held": False}, [], []

    def acquire(path, *, role, phase, **kwargs):
        return observed_lock(path, role=role, phase=phase, state=state, events=events, **kwargs)

    monkeypatch.setattr(universe, "writer_lock", acquire, raising=False)
    monkeypatch.setattr(backfill, "writer_lock", acquire, raising=False)

    class Broker:
        async def fetch(self, kind):
            assert_unlocked(db, state)
            await asyncio.sleep(0)
            assert_unlocked(db, state)
            fetched.append(kind)
            return [{**BROKER_ROW, "figi": f"FCOORD-{kind}", "class": kind}]

        async def get_shares(self):
            return await self.fetch("share")

        async def get_bonds(self):
            return await self.fetch("bond")

        async def get_etfs(self):
            return await self.fetch("etf")

        async def get_futures(self):
            raise AssertionError("non-tradeable-fetch")

        async def get_options(self):
            raise AssertionError("non-tradeable-fetch")

    if use_runner:
        runner = backfill.BackfillRunner(client=Broker(), db_path=db, event_sink=offline_sink)
        assert await runner._discover_universe() == 3
        expected = [("backfill-metadata", "instruments"), ("backfill-metadata", "metadata")] * 3
    else:
        assert await run_universe_sync(db, Broker()) == 3
        expected = [("universe-sync", "instruments")] * 3
    assert sorted(fetched) == ["bond", "etf", "share"]
    assert [(event[1], event[2]) for event in events if event[0] == "acquire"] == expected
    con = real_connect(db)
    try:
        assert con.execute(
            "SELECT figi, class, name, lot_size FROM instruments ORDER BY figi"
        ).fetchall() == [
            ("FCOORD", "share", "Original", 1),
            ("FCOORD-bond", "bond", "Updated", 10),
            ("FCOORD-etf", "etf", "Updated", 10),
            ("FCOORD-share", "share", "Updated", 10),
        ]
        metadata = con.execute(
            "SELECT figi, total_bars, last_run_status FROM instrument_metadata ORDER BY figi"
        ).fetchall()
        assert metadata == ([("FCOORD-bond", 0, "pending"), ("FCOORD-etf", 0, "pending"),
                             ("FCOORD-share", 0, "pending")] if use_runner else [])
    finally:
        con.close()
    assert_unlocked(db, state)


@pytest.mark.parametrize("owner,expected_isin,expected_sector", [
    ("universe", None, None), ("instrument", "ISIN-OLD", "Old sector"),
], ids=["universe-clears-absent", "metadata-preserves-absent"])
def test_universe_or_metadata_absent_broker_fields_keep_distinct_policies(
    coordinated_db, owner_trace, owner, expected_isin, expected_sector,
):
    db, real_connect = coordinated_db
    _, _, _, install = owner_trace
    install()
    row = {key: value for key, value in BROKER_ROW.items() if key not in {"isin", "sector"}}
    invoke_owner(owner, db, row)
    con = real_connect(db)
    try:
        assert con.execute(
            "SELECT isin, sector, expected_bars, source_updated_at, listed_till FROM instruments"
        ).fetchone() == (expected_isin, expected_sector, 123, "2020-01-02", "2024-05-31")
    finally:
        con.close()


def test_metadata_seed_never_resets_existing_progress(coordinated_db, owner_trace):
    db, real_connect = coordinated_db
    _, _, _, install = owner_trace
    prepare_metadata_update(db, real_connect, "update")
    before = snapshot(db, real_connect)
    install()
    assert invoke_owner("seed", db) is None
    assert snapshot(db, real_connect) == before


@pytest.mark.parametrize("status,error_msg", [("error", "fixture-fetch-error"), ("skipped", None), ("partial", None)])
def test_metadata_update_non_ok_preserves_existing_semantics(coordinated_db, owner_trace, status, error_msg):
    db, real_connect = coordinated_db
    _, _, _, install = owner_trace
    prepare_metadata_update(db, real_connect, "update")
    install()
    assert invoke_owner("update", db, status=status, error_msg=error_msg) is None
    con = real_connect(db)
    try:
        assert con.execute(
            "SELECT last_bar_ts, total_bars, last_backfilled_at, last_run_status, last_run_at, "
            "last_error, first_bar_ts, tinkoff_consecutive_failures, tinkoff_breaker_open FROM instrument_metadata"
        ).fetchone() == ("2024-05-15", 1, None, status, FIXED_NOW.isoformat(), error_msg,
                        "2024-01-01", 2, 1)
    finally:
        con.close()


@pytest.mark.parametrize("owner,role,phase", OWNER_CASES)
def test_universe_or_metadata_uses_existing_same_path_guard(coordinated_db, owner_trace, owner, role, phase):
    db, real_connect = coordinated_db
    state, events, connections, install = owner_trace
    prepare_metadata_update(db, real_connect, owner)
    before = snapshot(db, real_connect)
    install()
    with locks.writer_lock(db, role="bar-writer", phase="bars", timeout_seconds=0.05):
        with pytest.raises(locks.WriterLockReentrant):
            invoke_owner(owner, db)
        assert events == [("close",)]
        assert snapshot(db, real_connect) == before
        assert_closed(connections)
    invoke_owner(owner, db)
    assert_saved(owner, db, real_connect)
    assert_unlocked(db, state)


def test_universe_does_not_change_existing_delete_journal_mode(coordinated_db, owner_trace):
    db, real_connect = coordinated_db
    _, _, connections, install = owner_trace
    con = real_connect(db)
    try:
        assert con.execute("PRAGMA journal_mode=DELETE").fetchone() == ("delete",)
    finally:
        con.close()
    install()
    assert universe.upsert_instruments(db, [BROKER_ROW]) == 1
    assert connections[0].settings_at_begin == (1, 30000, "delete")
    assert_saved("universe", db, real_connect)
    con = real_connect(db)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    finally:
        con.close()


@pytest.mark.parametrize("rows", [[], [{**BROKER_ROW, "class": "future"}]])
def test_universe_empty_filtered_input_has_no_connection_or_acquisition(coordinated_db, owner_trace, rows):
    db, real_connect = coordinated_db
    _, events, connections, install = owner_trace
    before = snapshot(db, real_connect)
    install()
    assert universe.upsert_instruments(db, rows) == 0
    assert events == []
    assert connections == []
    assert snapshot(db, real_connect) == before
    assert not locks.writer_lock_path(db).exists()


@pytest.mark.parametrize("row", [{}, {"figi": "FCOORD"}, {"ticker": "COORD"}])
def test_metadata_invalid_identity_has_no_connection_or_acquisition(coordinated_db, owner_trace, row):
    db, real_connect = coordinated_db
    _, events, connections, install = owner_trace
    before = snapshot(db, real_connect)
    install()
    assert invoke_owner("instrument", db, row) is None
    assert events == []
    assert connections == []
    assert snapshot(db, real_connect) == before
    assert not locks.writer_lock_path(db).exists()


def test_metadata_instrument_defaults_remain_unchanged(coordinated_db, owner_trace):
    db, real_connect = coordinated_db
    _, events, _, install = owner_trace
    install()
    assert invoke_owner("instrument", db, {"ticker": "COORD", "figi": "FCOORD", "class": "share"}) is None
    con = real_connect(db)
    try:
        assert con.execute(
            "SELECT name, currency, lot_size, isin, sector, expected_bars FROM instruments"
        ).fetchone() == ("", "", 0, "ISIN-OLD", "Old sector", 123)
    finally:
        con.close()
    assert events == [("acquire", "backfill-metadata", "instruments"), ("BEGIN",),
                      ("INSERT",), ("commit",), ("release", "backfill-metadata", "instruments"), ("close",)]


CORPORATE_OWNERS = [
    pytest.param("merge", "corporate-actions", id="corporate-merge"),
    pytest.param("adjustment", "adjusted-bars", id="worker-adjustment"),
]
SPLIT_ROW = corporate.CorporateActionRow(
    "FCOORD", "split", date(2024, 5, 15), 2.0, 0.0, source="derived:fixture",
)
SECOND_SPLIT_ROW = corporate.CorporateActionRow(
    "FCOORD", "split", "2024-05-16", 4.0, 0.0, note="second", source="derived:second",
)


@pytest.fixture
def corporate_db(coordinated_db):
    db, real_connect = coordinated_db
    con = real_connect(db)
    try:
        con.execute("UPDATE bars SET open=51, high=52, low=49 WHERE figi='FCOORD'")
        con.executemany(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [("FCOORD", "2024-05-13", 99, 101, 98, 100, 100),
             ("FCOORD", "2024-05-16", 50, 52, 49, 51, 100),
             ("FOTHER", "2024-05-16", 18, 18, 18, 18, 7)],
        )
        con.execute(
            "INSERT INTO bars_adjusted VALUES ('FOTHER', '2024-05-16', 9, 9, 9, 9, 7, 'fixture', 'fixed')"
        )
        con.commit()
    finally:
        con.close()
    return db, real_connect


@pytest.fixture
def corporate_trace(corporate_db, owner_trace, monkeypatch):
    db, _ = corporate_db
    state, events, connections, base_install = owner_trace
    worker = importlib.import_module("worker")

    @contextmanager
    def acquire(path, *, role, phase, **kwargs):
        try:
            with observed_lock(path, role=role, phase=phase, state=state, events=events, **kwargs):
                yield
        except BaseException as exc:
            # Observe the transaction error before the existing worker adapter catches it.
            state["error"] = exc
            raise

    def observe_sql(conn, sql, parameters):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT 1 FROM corporate_actions"):
            assert state["held"] and conn.in_transaction
            events.append(("duplicate-check",))
        elif normalized.startswith("SELECT figi, ex_date, factor FROM corporate_actions"):
            assert not state["held"] and not conn.in_transaction
            events.append(("prepare-select",))
        elif normalized.startswith("SELECT ba.adj_close, b.close"):
            assert state["held"] and conn.in_transaction
            events.append(("already-applied",))
        elif normalized.startswith(("SELECT DISTINCT figi FROM bars", "SELECT ts, close, volume FROM bars")):
            assert not state["held"]
            events.append(("derive-select",))

    def install(*, real_derivation=False, **kwargs):
        base_install(sql_observer=observe_sql, **kwargs)
        monkeypatch.setattr(corporate, "writer_lock", acquire, raising=False)
        monkeypatch.setattr(worker, "writer_lock", acquire, raising=False)
        if not real_derivation:
            monkeypatch.setattr(derive_splits, "run_derivation", lambda path: 0)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return FIXED_NOW

        monkeypatch.setattr(forward_adjustment, "datetime", FixedDatetime)

    return state, events, connections, install, worker


def seed_corporate_actions(db, real_connect, rows):
    con = real_connect(db)
    try:
        con.executemany(
            "INSERT INTO corporate_actions (figi, action_type, ex_date, factor, cash_amount, note, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r.figi, r.action_type, r.ex_date.isoformat() if isinstance(r.ex_date, date) else r.ex_date,
              r.factor, r.cash_amount, r.note, r.source) for r in rows],
        )
        con.commit()
    finally:
        con.close()


def call_corporate_owner(owner, db, worker, rows):
    if owner == "merge":
        return corporate.merge_into_corporate_actions(db, rows)
    return worker._step_corporate_actions(db)


def corporate_error(owner, db, worker, rows, state):
    if owner == "merge":
        with pytest.raises(BaseException) as caught:
            call_corporate_owner(owner, db, worker, rows)
        return caught.value
    try:
        result = call_corporate_owner(owner, db, worker, rows)
    except BaseException as exc:
        return exc
    assert isinstance(result, tuple) and result[0] is False
    # Task 5 owns the adapter change; Task 3 observes the escaped owner error only.
    assert "error" in state, "owner never entered observed writer lock"
    return state["error"]


def corporate_transaction_events(owner, phase, *, two_rows=False, outcome="commit"):
    mutation = ([("duplicate-check",), ("INSERT",)] if owner == "merge" else
                [("already-applied",), ("INSERT",), ("UPDATE",)])
    return [*([("prepare-select",)] if owner == "adjustment" else []),
            ("acquire", "corporate-actions", phase), ("BEGIN",),
            *mutation, *(mutation if two_rows else []), (outcome,),
            ("release", "corporate-actions", phase), ("close",)]


def assert_adjustment_saved(db, real_connect):
    con = real_connect(db)
    try:
        assert con.execute(
            "SELECT figi, ts, adj_open, adj_high, adj_low, adj_close, adj_volume, source, computed_at "
            "FROM bars_adjusted ORDER BY figi, ts"
        ).fetchall() == [
            ("FCOORD", "2024-05-15", 25.5, 26.0, 24.5, 25.0, 100,
             "derived:bars+forward", "2024-06-01T12:00:00+00:00"),
            ("FCOORD", "2024-05-16", 25.0, 26.0, 24.5, 25.5, 100,
             "derived:bars+forward", "2024-06-01T12:00:00+00:00"),
            ("FOTHER", "2024-05-16", 9.0, 9.0, 9.0, 9.0, 7, "fixture", "fixed"),
        ]
    finally:
        con.close()


@pytest.mark.parametrize("owner,phase", CORPORATE_OWNERS)
def test_corporate_merge_or_adjustment_transaction_order(corporate_db, corporate_trace, owner, phase):
    db, real_connect = corporate_db
    state, events, connections, install, worker = corporate_trace
    if owner == "adjustment":
        seed_corporate_actions(db, real_connect, [SPLIT_ROW])
    before = snapshot(db, real_connect)
    install()
    assert call_corporate_owner(owner, db, worker, [SPLIT_ROW]) == (
        1 if owner == "merge" else (True, "splits derived=0 bars adjusted=2")
    )
    assert events == corporate_transaction_events(owner, phase)
    assert connections[0].settings_at_begin == (0, 5000, "wal")
    after = snapshot(db, real_connect)
    assert after["bars"] == before["bars"]
    if owner == "merge":
        assert after["corporate_actions"] == [("FCOORD", "split", "2024-05-15", 2.0, 0.0, "", "derived:fixture")]
        assert after["bars_adjusted"] == before["bars_adjusted"]
    else:
        assert after["corporate_actions"] == before["corporate_actions"]
        assert_adjustment_saved(db, real_connect)
    assert_closed(connections)
    assert_unlocked(db, state)


@pytest.mark.parametrize("owner,phase", CORPORATE_OWNERS)
def test_corporate_merge_or_adjustment_flock_timeout_is_retryable(corporate_db, corporate_trace, owner, phase):
    db, real_connect = corporate_db
    state, events, connections, install, worker = corporate_trace
    if owner == "adjustment":
        seed_corporate_actions(db, real_connect, [SPLIT_ROW])
    before = snapshot(db, real_connect)
    install()
    with open(locks.writer_lock_path(db), "a+b") as descriptor:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        exc = corporate_error(owner, db, worker, [SPLIT_ROW], state)
        assert isinstance(exc, locks.WriterLockBusy)
        assert_busy(exc, db, "corporate-actions", phase, reason="flock-timeout", timeout=0.05)
        assert events == ([ ("prepare-select",)] if owner == "adjustment" else []) + [("close",)]
        assert snapshot(db, real_connect) == before
        assert_closed(connections)
    events.clear()
    assert call_corporate_owner(owner, db, worker, [SPLIT_ROW]) == (
        1 if owner == "merge" else (True, "splits derived=0 bars adjusted=2")
    )
    assert events == corporate_transaction_events(owner, phase)
    assert_closed(connections)
    assert_unlocked(db, state)


@pytest.mark.parametrize("owner,phase", CORPORATE_OWNERS)
def test_corporate_merge_or_adjustment_real_sqlite_busy(corporate_db, corporate_trace, owner, phase):
    db, real_connect = corporate_db
    state, events, connections, install, worker = corporate_trace
    if owner == "adjustment":
        seed_corporate_actions(db, real_connect, [SPLIT_ROW])
    before = snapshot(db, real_connect)
    blocker = real_connect(db)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        install(sqlite_timeout=0.05)
        exc = corporate_error(owner, db, worker, [SPLIT_ROW], state)
        assert isinstance(exc, locks.WriterLockBusy)
        assert_busy(exc, db, "corporate-actions", phase, reason="sqlite-busy", timeout=30.0)
        assert isinstance(exc.__cause__, sqlite3.OperationalError)
        assert exc.__cause__.sqlite_errorcode == sqlite3.SQLITE_BUSY
        assert events == [*([("prepare-select",)] if owner == "adjustment" else []),
                          ("acquire", "corporate-actions", phase), ("BEGIN",), ("rollback",),
                          ("release", "corporate-actions", phase), ("close",)]
        assert snapshot(db, real_connect) == before
        assert_closed(connections)
        assert_unlocked(db, state)
    finally:
        blocker.rollback()
        blocker.close()
    assert call_corporate_owner(owner, db, worker, [SPLIT_ROW]) == (
        1 if owner == "merge" else (True, "splits derived=0 bars adjusted=2")
    )


@pytest.mark.parametrize("owner,phase", CORPORATE_OWNERS)
@pytest.mark.parametrize("primary_type", [sqlite3.IntegrityError, Interrupted])
@pytest.mark.parametrize("broken_rollback", [False, True])
def test_corporate_merge_or_adjustment_complete_list_commit_failure(
    corporate_db, corporate_trace, owner, phase, primary_type, broken_rollback,
):
    db, real_connect = corporate_db
    state, events, connections, install, worker = corporate_trace
    rows = [SPLIT_ROW, SECOND_SPLIT_ROW]
    if owner == "adjustment":
        seed_corporate_actions(db, real_connect, list(reversed(rows)))
    before = snapshot(db, real_connect)
    primary = primary_type("fixture-corporate-commit-failure")
    install(failure=primary, rollback_failure=Interrupted("fixture-rollback-failure") if broken_rollback else None)
    assert corporate_error(owner, db, worker, rows, state) is primary
    expected = corporate_transaction_events(owner, phase, two_rows=True)
    expected.insert(-2, ("rollback",))
    assert events == expected
    assert connections[0]._injected_rollback_failure is broken_rollback
    assert snapshot(db, real_connect) == before
    assert_closed(connections)
    assert_unlocked(db, state)
    install()
    assert call_corporate_owner(owner, db, worker, rows) == (
        2 if owner == "merge" else (True, "splits derived=0 bars adjusted=3")
    )
    assert_closed(connections)


@pytest.mark.parametrize("owner,phase", CORPORATE_OWNERS)
@pytest.mark.parametrize("code", [5, 517, 1, 6, None], ids=["busy", "extended-busy", "error", "locked", "text-only"])
def test_corporate_merge_or_adjustment_injected_commit_busy_is_numeric_only(
    corporate_db, corporate_trace, owner, phase, code,
):
    db, real_connect = corporate_db
    state, events, connections, install, worker = corporate_trace
    if owner == "adjustment":
        seed_corporate_actions(db, real_connect, [SPLIT_ROW])
    before = snapshot(db, real_connect)
    primary = sqlite3.OperationalError("database is locked")
    if code is not None:
        primary.sqlite_errorcode = code
    install(failure=primary)
    exc = corporate_error(owner, db, worker, [SPLIT_ROW], state)
    if code in (5, 517):
        assert isinstance(exc, locks.WriterLockBusy)
        assert_busy(exc, db, "corporate-actions", phase, reason="sqlite-busy", timeout=30.0)
        assert exc.__cause__ is primary
    else:
        assert exc is primary
    expected = corporate_transaction_events(owner, phase)
    expected.insert(-2, ("rollback",))
    assert events == expected
    assert snapshot(db, real_connect) == before
    assert_closed(connections)
    assert_unlocked(db, state)


@pytest.mark.parametrize("owner,phase", CORPORATE_OWNERS)
@pytest.mark.parametrize("location", ["BEGIN", "first-mutation", "second-mutation"])
def test_corporate_merge_or_adjustment_statement_interruption_is_atomic(
    corporate_db, corporate_trace, owner, phase, location,
):
    db, real_connect = corporate_db
    state, events, connections, install, worker = corporate_trace
    rows = [SPLIT_ROW, SECOND_SPLIT_ROW]
    if owner == "adjustment":
        seed_corporate_actions(db, real_connect, rows)
    before = snapshot(db, real_connect)
    primary = Interrupted("fixture-corporate-statement-interrupted")
    operation = "BEGIN" if location == "BEGIN" else ("INSERT" if owner == "merge" else "UPDATE")
    install(failure=primary, fail_operation=operation, failure_at_statement=2 if location == "second-mutation" else 1)
    assert corporate_error(owner, db, worker, rows, state) is primary
    assert events[-3:] == [("rollback",), ("release", "corporate-actions", phase), ("close",)]
    assert events.count(("BEGIN",)) == 1
    assert ("commit",) not in events
    assert snapshot(db, real_connect) == before
    assert_closed(connections)
    assert_unlocked(db, state)


def test_corporate_merge_same_pk_changed_factor_stays_skipped(corporate_db, corporate_trace):
    db, real_connect = corporate_db
    _, events, connections, install, _ = corporate_trace
    install()
    assert corporate.merge_into_corporate_actions(db, [SPLIT_ROW, SECOND_SPLIT_ROW, SPLIT_ROW]) == 2
    first = snapshot(db, real_connect)
    changed = corporate.CorporateActionRow("FCOORD", "split", "2024-05-15", 10.0, 8.0, "changed", "changed")
    assert corporate.merge_into_corporate_actions(db, [changed, SECOND_SPLIT_ROW]) == 0
    assert snapshot(db, real_connect) == first
    assert events.count(("acquire", "corporate-actions", "corporate-actions")) == 2
    assert events.count(("INSERT",)) == 2
    assert_closed(connections)


def test_corporate_merge_empty_input_has_no_connection_or_acquisition(corporate_db, corporate_trace):
    db, real_connect = corporate_db
    _, events, connections, install, _ = corporate_trace
    before = snapshot(db, real_connect)
    install()
    assert corporate.merge_into_corporate_actions(db, []) == 0
    assert events == [] and connections == []
    assert snapshot(db, real_connect) == before
    assert not locks.writer_lock_path(db).exists()


def test_corporate_merge_prepares_every_field_and_date_before_acquisition(corporate_db, corporate_trace):
    db, _ = corporate_db
    state, events, _, install, _ = corporate_trace

    class PreparedRow(corporate.CorporateActionRow):
        def __getattribute__(self, name):
            if name in {"figi", "action_type", "ex_date", "factor", "cash_amount", "note", "source"}:
                assert not state["held"], f"row field {name} inside owner lock"
                events.append(("parameter", name))
            return super().__getattribute__(name)

    class PreparedDate(date):
        def isoformat(self):
            assert not state["held"]
            events.append(("date-iso",))
            return super().isoformat()

    rows: list[corporate.CorporateActionRow] = [PreparedRow("FCOORD", "split", PreparedDate(2024, 5, 15), 2.0, 0.0, source="derived:fixture"),
            PreparedRow("FCOORD", "split", "2024-05-16", 4.0, 0.0, source="derived:second")]
    install()
    assert corporate.merge_into_corporate_actions(db, rows) == 2
    acquisition = events.index(("acquire", "corporate-actions", "corporate-actions"))
    assert all(event[0] not in {"parameter", "date-iso"} for event in events[acquisition:])
    assert {event[1] for event in events[:acquisition] if event[0] == "parameter"} == {
        "figi", "action_type", "ex_date", "factor", "cash_amount", "note", "source",
    }


def test_corporate_adjustment_prepares_all_events_before_acquisition(corporate_db, corporate_trace, monkeypatch):
    db, real_connect = corporate_db
    state, events, _, install, worker = corporate_trace
    seed_corporate_actions(db, real_connect, [SECOND_SPLIT_ROW, SPLIT_ROW])
    install()

    class PreparedDate(date):
        @classmethod
        def fromisoformat(cls, value):
            assert not state["held"]
            events.append(("prepare-date", value))
            return date.fromisoformat(value)

    def prepare_float(value):
        assert not state["held"]
        events.append(("prepare-float", value))
        return float(value)

    monkeypatch.setattr(worker, "date", PreparedDate)
    monkeypatch.setattr(worker, "float", prepare_float, raising=False)
    assert worker._step_corporate_actions(db) == (True, "splits derived=0 bars adjusted=3")
    assert events[:5] == [("prepare-select",), ("prepare-date", "2024-05-15"), ("prepare-float", 2.0),
                          ("prepare-date", "2024-05-16"), ("prepare-float", 4.0)]
    assert events[5:] == corporate_transaction_events("adjustment", "adjusted-bars", two_rows=True)[1:]
    con = real_connect(db)
    try:
        assert con.execute(
            "SELECT ts, adj_open, adj_high, adj_low, adj_close, adj_volume FROM bars_adjusted WHERE figi='FCOORD' ORDER BY ts"
        ).fetchall() == [("2024-05-15", 25.5, 26.0, 24.5, 25.0, 100),
                        ("2024-05-16", 6.25, 6.5, 6.125, 6.375, 100)]
    finally:
        con.close()


@pytest.mark.parametrize("field,value", [("ex_date", "invalid-date"), ("factor", "invalid-factor")])
def test_corporate_adjustment_bad_later_event_fails_before_any_write(corporate_db, corporate_trace, field, value):
    db, real_connect = corporate_db
    _, events, connections, install, worker = corporate_trace
    seed_corporate_actions(db, real_connect, [SPLIT_ROW, SECOND_SPLIT_ROW])
    con = real_connect(db)
    try:
        con.execute(f"UPDATE corporate_actions SET {field}=? WHERE ex_date='2024-05-16'", (value,))
        con.commit()
    finally:
        con.close()
    before = snapshot(db, real_connect)
    install()
    ok, detail = worker._step_corporate_actions(db)
    assert ok is False
    assert ("Invalid isoformat string" if field == "ex_date" else "could not convert string to float") in detail
    assert events == [("prepare-select",), ("close",)]
    assert not locks.writer_lock_path(db).exists()
    assert snapshot(db, real_connect) == before
    assert_closed(connections)


def test_corporate_adjustment_single_split_retry_is_exactly_idempotent(corporate_db, corporate_trace):
    db, real_connect = corporate_db
    _, events, connections, install, worker = corporate_trace
    seed_corporate_actions(db, real_connect, [SPLIT_ROW])
    install()
    assert worker._step_corporate_actions(db) == (True, "splits derived=0 bars adjusted=2")
    first = snapshot(db, real_connect)
    events.clear()
    assert worker._step_corporate_actions(db) == (True, "splits derived=0 bars adjusted=0")
    assert snapshot(db, real_connect) == first
    assert events == [("prepare-select",), ("acquire", "corporate-actions", "adjusted-bars"), ("BEGIN",),
                      ("already-applied",), ("commit",), ("release", "corporate-actions", "adjusted-bars"), ("close",)]
    assert_closed(connections)


@pytest.mark.parametrize("owner,phase", CORPORATE_OWNERS)
def test_corporate_merge_or_adjustment_existing_same_path_guard(corporate_db, corporate_trace, owner, phase):
    db, real_connect = corporate_db
    state, events, connections, install, worker = corporate_trace
    if owner == "adjustment":
        seed_corporate_actions(db, real_connect, [SPLIT_ROW])
    before = snapshot(db, real_connect)
    install()
    with locks.writer_lock(db, role="bar-writer", phase="bars", timeout_seconds=0.05):
        assert isinstance(corporate_error(owner, db, worker, [SPLIT_ROW], state), locks.WriterLockReentrant)
        assert events == ([ ("prepare-select",)] if owner == "adjustment" else []) + [("close",)]
        assert snapshot(db, real_connect) == before
        assert_closed(connections)
    assert_unlocked(db, state)


@pytest.mark.parametrize("interrupt_adjustment", [False, True], ids=["success", "adjustment-interrupted"])
def test_corporate_merge_real_derivation_releases_before_adjustment(
    corporate_db, corporate_trace, monkeypatch, interrupt_adjustment,
):
    db, real_connect = corporate_db
    state, events, connections, install, worker = corporate_trace
    before = snapshot(db, real_connect)
    original_derive = derive_splits.derive_splits_for_figi

    def derive(**kwargs):
        assert_unlocked(db, state)
        time.sleep(0)
        return original_derive(**kwargs)

    monkeypatch.setattr(derive_splits, "derive_splits_for_figi", derive)
    primary = Interrupted("fixture-adjustment-interrupted")
    install(real_derivation=True, failure=primary if interrupt_adjustment else None, fail_operation="UPDATE")
    if interrupt_adjustment:
        with pytest.raises(Interrupted) as caught:
            worker._step_corporate_actions(db)
        assert caught.value is primary
    else:
        assert worker._step_corporate_actions(db) == (True, "splits derived=1 bars adjusted=2")
        assert_adjustment_saved(db, real_connect)
    after = snapshot(db, real_connect)
    assert after["bars"] == before["bars"]
    assert after["corporate_actions"] == [
        ("FCOORD", "split", "2024-05-15", 2.0, 0.0, "derived from bars ratio=0.5000",
         "derived:bars+facevalue:2024-05-13:2024-05-16"),
    ]
    assert [(e[1], e[2]) for e in events if e[0] == "acquire"] == [
        ("corporate-actions", "corporate-actions"), ("corporate-actions", "adjusted-bars"),
    ]
    assert events.index(("release", "corporate-actions", "corporate-actions")) < events.index(("prepare-select",))
    assert events.index(("prepare-select",)) < events.index(("acquire", "corporate-actions", "adjusted-bars"))
    if interrupt_adjustment:
        assert after["bars_adjusted"] == before["bars_adjusted"]
        assert events[-3:] == [("rollback",), ("release", "corporate-actions", "adjusted-bars"), ("close",)]
        install()
        assert worker._step_corporate_actions(db) == (True, "splits derived=0 bars adjusted=2")
        assert_adjustment_saved(db, real_connect)
    assert_closed(connections)
    assert_unlocked(db, state)


@pytest.mark.parametrize("factor", [None, 1.0], ids=["no-split", "factor-one"])
def test_corporate_adjustment_no_mutation_still_owns_one_transaction(corporate_db, corporate_trace, factor):
    db, real_connect = corporate_db
    state, events, connections, install, worker = corporate_trace
    if factor is not None:
        seed_corporate_actions(db, real_connect, [corporate.CorporateActionRow(
            "FCOORD", "split", "2024-05-15", factor, 0.0, source="derived:fixture",
        )])
    before = snapshot(db, real_connect)
    install()
    assert worker._step_corporate_actions(db) == (True, "splits derived=0 bars adjusted=0")
    assert events == [("prepare-select",), ("acquire", "corporate-actions", "adjusted-bars"), ("BEGIN",),
                      ("commit",), ("release", "corporate-actions", "adjusted-bars"), ("close",)]
    assert snapshot(db, real_connect) == before
    assert_closed(connections)
    assert_unlocked(db, state)


DIVIDEND_ROW = corporate.DividendRow(
    figi="FCOORD", ex_date="2024-06-15", period_year=2024,
    amount_per_share=10.0, retrieved_at="2024-09-14T12:00:00",
)
DIVIDEND_STORED = (
    "FCOORD", "2024-06-15", None, None, None, 2024, 1, "rub", 10.0,
    None, "regular", None, None, None, None, None, None, "tinkoff", None,
    "2024-09-14T12:00:00", 1, 0, 10.0, None,
)
DIVIDEND_REVISION = corporate.DividendRow(
    figi="FCOORD", ex_date="2024-06-15", period_year=2024,
    pay_date="2024-07-01", record_date="2024-06-20",
    declared_at="2024-03-15T10:00:00Z", period_no=1, currency="usd",
    amount_per_share=12.5, fx_rate_used=2.0, dividend_type="interim",
    regularity="semi-annual", close_price=100.0, yield_value=2.5,
    yield_pct=0.025, tax_withheld_pct=0.13,
    cancelled_at="2024-09-13T09:30:00+03:00", source="fixture:revision",
    source_revision_ts="2024-09-13T10:00:00Z",
    retrieved_at="2024-09-14T12:00:01", revision_n=2, note="corrected",
)
DIVIDEND_REVISION_STORED = (
    "FCOORD", "2024-06-15", "2024-07-01", "2024-06-20",
    "2024-03-15T10:00:00Z", 2024, 1, "usd", 12.5, 2.0, "interim",
    "semi-annual", 100.0, 2.5, 0.025, 0.13,
    "2024-09-13T09:30:00+03:00", "fixture:revision",
    "2024-09-13T10:00:00Z", "2024-09-14T12:00:01", 2, 1, 25.0, "corrected",
)
DIVIDEND_FIELDS = (
    "figi", "ex_date", "pay_date", "record_date", "declared_at", "period_year",
    "period_no", "currency", "amount_per_share", "fx_rate_used", "dividend_type",
    "regularity", "close_price", "yield_value", "yield_pct", "tax_withheld_pct",
    "cancelled_at", "source", "source_revision_ts", "retrieved_at", "revision_n", "note",
)


@pytest.fixture
def dividend_trace(coordinated_db, owner_trace, monkeypatch):
    state, events, connections, base_install = owner_trace

    def acquire(path, *, role, phase, **kwargs):
        assert (role, phase) == ("dividends", "dividends")
        return observed_lock(path, role=role, phase=phase, state=state, events=events, **kwargs)

    def observe_sql(conn, sql, parameters):
        normalized = " ".join(sql.split())
        assert "corporate_actions" not in normalized
        assert "dividends_throttle_pending" not in normalized
        if normalized.startswith("SELECT 1 FROM dividends"):
            assert state["held"] and conn.in_transaction, "dividend PK check outside owner transaction"
            events.append(("duplicate-check",))
        elif normalized.startswith("INSERT INTO dividends"):
            assert state["held"] and conn.in_transaction
            assert len(parameters) == 22

    def install(**kwargs):
        base_install(sql_observer=observe_sql, **kwargs)
        monkeypatch.setattr(corporate, "writer_lock", acquire)

    return state, events, connections, install


def dividend_transaction_events(*, inserts=1, checks=None, rollback=False):
    checks = inserts if checks is None else checks
    return [
        ("acquire", "dividends", "dividends"), ("BEGIN",),
        *[(event,) for index in range(checks)
          for event in (["duplicate-check", "INSERT"] if index < inserts else ["duplicate-check"])],
        ("commit",), *([("rollback",)] if rollback else []),
        ("release", "dividends", "dividends"), ("close",),
    ]


@pytest.mark.parametrize("journal", ["wal", "delete"])
def test_dividend_merge_complete_list_transaction_order(coordinated_db, dividend_trace, journal):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    con = real_connect(db)
    try:
        assert con.execute(f"PRAGMA journal_mode={journal}").fetchone() == (journal,)
    finally:
        con.close()
    before = snapshot(db, real_connect)
    install()
    assert corporate.merge_into_dividends(db, [DIVIDEND_ROW, DIVIDEND_REVISION]) == 2
    assert events == dividend_transaction_events(inserts=2)
    assert len(connections) == 1
    assert connections[0].settings_at_begin == (0, 5000, journal)
    after = snapshot(db, real_connect)
    assert after == {**before, "dividends": [DIVIDEND_STORED, DIVIDEND_REVISION_STORED]}
    assert_closed(connections)
    assert_unlocked(db, state)


def test_dividend_merge_same_pk_changed_amount_stays_skipped(coordinated_db, dividend_trace):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)
    install()
    assert corporate.merge_into_dividends(db, [DIVIDEND_ROW, DIVIDEND_ROW]) == 1
    assert events == dividend_transaction_events(inserts=1, checks=2)
    first = snapshot(db, real_connect)
    assert first == {**before, "dividends": [DIVIDEND_STORED]}
    events.clear()
    changed = replace(DIVIDEND_ROW, amount_per_share=999.0, source="changed",
                      retrieved_at="2025-01-01T00:00:00", note="changed")
    assert corporate.merge_into_dividends(db, [changed, DIVIDEND_ROW]) == 0
    assert snapshot(db, real_connect) == first
    assert events == dividend_transaction_events(inserts=0, checks=2)
    assert_closed(connections)
    assert_unlocked(db, state)


def test_dividend_merge_revisions_remain_distinct_and_retry_idempotent(coordinated_db, dividend_trace):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)
    install()
    assert corporate.merge_into_dividends(db, [DIVIDEND_ROW, DIVIDEND_REVISION, DIVIDEND_REVISION]) == 2
    first = snapshot(db, real_connect)
    assert first == {**before, "dividends": [DIVIDEND_STORED, DIVIDEND_REVISION_STORED]}
    assert events == dividend_transaction_events(inserts=2, checks=3)
    events.clear()
    assert corporate.merge_into_dividends(db, [DIVIDEND_REVISION, DIVIDEND_ROW]) == 0
    assert events == dividend_transaction_events(inserts=0, checks=2)
    assert snapshot(db, real_connect) == first
    assert_closed(connections)
    assert_unlocked(db, state)


@pytest.mark.parametrize("field,value,column", [
    ("figi", "FOTHER", 0), ("ex_date", "2024-06-16", 1),
    ("period_year", 2023, 5), ("period_no", 2, 6), ("revision_n", 2, 20),
])
def test_dividend_merge_preserves_each_pk_component(coordinated_db, dividend_trace, field, value, column):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)
    changed = replace(DIVIDEND_ROW, **{field: value})
    expected = list(DIVIDEND_STORED)
    expected[column] = value
    if field == "revision_n":
        expected[21] = 1
    install()
    assert corporate.merge_into_dividends(db, [DIVIDEND_ROW, changed]) == 2
    after = snapshot(db, real_connect)
    assert sorted(after["dividends"]) == sorted([DIVIDEND_STORED, tuple(expected)])
    assert {table: rows for table, rows in after.items() if table != "dividends"} == {
        table: rows for table, rows in before.items() if table != "dividends"
    }
    assert events == dividend_transaction_events(inserts=2)
    assert_closed(connections)
    assert_unlocked(db, state)


def test_dividend_merge_empty_input_has_no_connection_or_acquisition(coordinated_db, dividend_trace):
    db, real_connect = coordinated_db
    _, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)
    install()
    assert corporate.merge_into_dividends(db, []) == 0
    assert events == [] and connections == []
    assert snapshot(db, real_connect) == before
    assert not locks.writer_lock_path(db).exists()


def test_dividend_merge_prepares_all_22_fields_before_connection_and_acquisition(
    coordinated_db, dividend_trace, monkeypatch,
):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)

    class PreparedRow(corporate.DividendRow):
        def __getattribute__(self, name):
            if name in DIVIDEND_FIELDS:
                assert not state["held"], f"row field {name} inside owner lock"
                assert ("acquire", "dividends", "dividends") not in events
                events.append(("parameter", name))
                time.sleep(0)
            return super().__getattribute__(name)

    rows: list[corporate.DividendRow] = [
        PreparedRow(**vars(DIVIDEND_ROW)), PreparedRow(**vars(DIVIDEND_REVISION)),
    ]
    events.clear()  # __post_init__ validates identity before merge preparation.
    install()
    tracked_connect = sqlite3.connect
    prepared_events = [("parameter", name) for name in DIVIDEND_FIELDS] * 2

    def connect(*args, **kwargs):
        assert events == prepared_events, "connection opened before complete row-list preparation"
        events.append(("open",))
        return tracked_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert corporate.merge_into_dividends(db, rows) == 2
    assert events == [*prepared_events, ("open",), *dividend_transaction_events(inserts=2)]
    assert snapshot(db, real_connect) == {
        **before, "dividends": [DIVIDEND_STORED, DIVIDEND_REVISION_STORED],
    }
    assert_closed(connections)
    assert_unlocked(db, state)


def test_dividend_merge_later_preparation_error_opens_no_connection(coordinated_db, dividend_trace):
    db, real_connect = coordinated_db
    _, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)
    primary = Interrupted("fixture-dividend-preparation-interrupted")

    class BrokenRow(corporate.DividendRow):
        def __getattribute__(self, name):
            if name == "note":
                raise primary
            return super().__getattribute__(name)

    rows = [DIVIDEND_ROW, BrokenRow(**vars(DIVIDEND_REVISION))]
    install()
    with pytest.raises(Interrupted) as caught:
        corporate.merge_into_dividends(db, rows)
    assert caught.value is primary
    assert events == [] and connections == []
    assert not locks.writer_lock_path(db).exists()
    assert snapshot(db, real_connect) == before


@pytest.mark.parametrize("primary_type", [sqlite3.IntegrityError, Interrupted])
@pytest.mark.parametrize("broken_rollback", [False, True], ids=["healthy-rollback", "rollback-failure"])
def test_dividend_merge_complete_list_commit_failure_preserves_primary(
    coordinated_db, dividend_trace, primary_type, broken_rollback,
):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)
    primary = primary_type("fixture-dividend-commit-failure")
    install(failure=primary, rollback_failure=Interrupted("fixture-rollback-failure") if broken_rollback else None)
    with pytest.raises(primary_type) as caught:
        corporate.merge_into_dividends(db, [DIVIDEND_ROW, DIVIDEND_REVISION])
    assert caught.value is primary
    assert events == dividend_transaction_events(inserts=2, rollback=True)
    assert connections[0]._injected_rollback_failure is broken_rollback
    assert snapshot(db, real_connect) == before
    assert_closed(connections)
    assert_unlocked(db, state)
    events.clear()
    install()
    assert corporate.merge_into_dividends(db, [DIVIDEND_ROW, DIVIDEND_REVISION]) == 2
    assert snapshot(db, real_connect) == {
        **before, "dividends": [DIVIDEND_STORED, DIVIDEND_REVISION_STORED],
    }
    assert events == dividend_transaction_events(inserts=2)
    assert_closed(connections)


@pytest.mark.parametrize("location,operation,statement", [
    ("begin", "BEGIN", 1), ("first-check", "SELECT", 1), ("second-check", "SELECT", 2),
    ("first-insert", "INSERT", 1), ("second-insert", "INSERT", 2),
])
@pytest.mark.parametrize("broken_rollback", [False, True], ids=["healthy-rollback", "rollback-failure"])
def test_dividend_merge_begin_and_body_interruption_is_atomic(
    coordinated_db, dividend_trace, location, operation, statement, broken_rollback,
):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)
    primary = Interrupted(f"fixture-dividend-{location}-interrupted")
    install(failure=primary, fail_operation=operation, failure_at_statement=statement,
            rollback_failure=Interrupted("fixture-rollback-failure") if broken_rollback else None)
    with pytest.raises(Interrupted) as caught:
        corporate.merge_into_dividends(db, [DIVIDEND_ROW, DIVIDEND_REVISION])
    assert caught.value is primary
    assert events[:2] == [("acquire", "dividends", "dividends"), ("BEGIN",)]
    assert events[-3:] == [("rollback",), ("release", "dividends", "dividends"), ("close",)]
    assert events.count(("BEGIN",)) == 1
    assert events.count(("INSERT",)) == (statement if operation == "INSERT" else
                                         (1 if location == "second-check" else 0))
    assert ("commit",) not in events
    assert connections[0]._injected_rollback_failure is broken_rollback
    assert snapshot(db, real_connect) == before
    assert_closed(connections)
    assert_unlocked(db, state)


def test_dividend_merge_real_second_insert_constraint_failure_rolls_back_complete_list(
    coordinated_db, dividend_trace,
):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)
    install()
    invalid = replace(DIVIDEND_REVISION, amount_per_share=None)
    with pytest.raises(sqlite3.IntegrityError) as caught:
        corporate.merge_into_dividends(db, [DIVIDEND_ROW, invalid])
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_NOTNULL
    assert events == [
        ("acquire", "dividends", "dividends"), ("BEGIN",),
        ("duplicate-check",), ("INSERT",), ("duplicate-check",), ("INSERT",),
        ("rollback",), ("release", "dividends", "dividends"), ("close",),
    ]
    assert snapshot(db, real_connect) == before
    assert_closed(connections)
    assert_unlocked(db, state)


@pytest.mark.parametrize("code", [5, 517, 1, 6, "5", None],
                         ids=["busy", "extended-busy", "error", "locked", "string-code", "text-only"])
@pytest.mark.parametrize("broken_rollback", [False, True], ids=["healthy-rollback", "rollback-failure"])
def test_dividend_merge_commit_busy_is_numeric_only(coordinated_db, dividend_trace, code, broken_rollback):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)
    primary = sqlite3.OperationalError("database is locked")
    if code is not None:
        primary.sqlite_errorcode = code
    install(failure=primary, rollback_failure=Interrupted("fixture-rollback-failure") if broken_rollback else None)
    with pytest.raises(locks.WriterLockBusy if code in (5, 517) else sqlite3.OperationalError) as caught:
        corporate.merge_into_dividends(db, [DIVIDEND_ROW, DIVIDEND_REVISION])
    if code in (5, 517):
        assert_busy(caught.value, db, "dividends", "dividends", reason="sqlite-busy", timeout=30.0)
        assert caught.value.__cause__ is primary
    else:
        assert caught.value is primary
    assert events == dividend_transaction_events(inserts=2, rollback=True)
    assert connections[0]._injected_rollback_failure is broken_rollback
    assert snapshot(db, real_connect) == before
    assert_closed(connections)
    assert_unlocked(db, state)


@pytest.mark.parametrize("alias", [False, True], ids=["canonical", "symlink"])
def test_dividend_merge_kernel_flock_timeout_is_retryable(coordinated_db, dividend_trace, alias):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    path = Path(db)
    if alias:
        path = path.with_name("alias.db")
        path.symlink_to(db)
    before = snapshot(db, real_connect)
    install()
    with open(locks.writer_lock_path(db), "a+b") as descriptor:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(locks.WriterLockBusy) as caught:
            corporate.merge_into_dividends(str(path), [DIVIDEND_ROW])
        assert_busy(caught.value, db, "dividends", "dividends", reason="flock-timeout", timeout=0.05)
        assert events == [("close",)]
        assert snapshot(db, real_connect) == before
        assert_closed(connections)
    events.clear()
    assert corporate.merge_into_dividends(str(path), [DIVIDEND_ROW]) == 1
    assert events == dividend_transaction_events()
    assert snapshot(db, real_connect) == {**before, "dividends": [DIVIDEND_STORED]}
    assert_closed(connections)
    assert_unlocked(db, state)


def test_dividend_merge_real_sqlite_busy_rolls_back_before_release(coordinated_db, dividend_trace):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)
    blocker = real_connect(db)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        install(sqlite_timeout=0.05)
        with pytest.raises(locks.WriterLockBusy) as caught:
            corporate.merge_into_dividends(db, [DIVIDEND_ROW, DIVIDEND_REVISION])
        assert_busy(caught.value, db, "dividends", "dividends", reason="sqlite-busy", timeout=30.0)
        assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
        assert caught.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_BUSY
        assert events == [("acquire", "dividends", "dividends"), ("BEGIN",), ("rollback",),
                          ("release", "dividends", "dividends"), ("close",)]
        assert snapshot(db, real_connect) == before
        assert_closed(connections)
        assert_unlocked(db, state)
    finally:
        blocker.rollback()
        blocker.close()
    events.clear()
    assert corporate.merge_into_dividends(db, [DIVIDEND_ROW, DIVIDEND_REVISION]) == 2
    assert events == dividend_transaction_events(inserts=2)
    assert snapshot(db, real_connect) == {
        **before, "dividends": [DIVIDEND_STORED, DIVIDEND_REVISION_STORED],
    }
    assert_closed(connections)


@pytest.mark.parametrize("alias", ["canonical", "symlink", "dotdot"])
@pytest.mark.parametrize("other_thread", [False, True], ids=["same-thread", "other-thread"])
def test_dividend_merge_same_pid_guard_covers_threads_and_path_aliases(
    coordinated_db, dividend_trace, alias, other_thread,
):
    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    path = Path(db)
    if alias == "symlink":
        path = path.with_name("alias.db")
        path.symlink_to(db)
    elif alias == "dotdot":
        directory = path.parent / "child"
        directory.mkdir()
        path = directory / ".." / path.name
    before = snapshot(db, real_connect)
    install()
    errors, cleanup_errors = [], []

    def attempt():
        try:
            corporate.merge_into_dividends(str(path), [DIVIDEND_ROW])
        except BaseException as exc:
            errors.append(exc)
        try:
            assert_closed(connections)
        except BaseException as exc:
            cleanup_errors.append(exc)

    with locks.writer_lock(db, role="bar-writer", phase="bars", timeout_seconds=0.05):
        if other_thread:
            thread = threading.Thread(target=attempt)
            thread.start()
            thread.join(timeout=1.0)
            assert not thread.is_alive(), "same-PID guard did not fail immediately"
        else:
            attempt()
        assert len(errors) == 1 and isinstance(errors[0], locks.WriterLockReentrant)
        assert cleanup_errors == []
        assert events == [("close",)]
        assert snapshot(db, real_connect) == before
    assert_unlocked(db, state)
    events.clear()
    assert corporate.merge_into_dividends(str(path), [DIVIDEND_ROW]) == 1
    assert events == dividend_transaction_events()
    assert snapshot(db, real_connect) == {**before, "dividends": [DIVIDEND_STORED]}
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[-1].execute("SELECT 1")


def test_dividend_merge_constructs_pk_parameter_tuple_before_acquisition(coordinated_db, dividend_trace):
    import dis
    import sys

    db, real_connect = coordinated_db
    state, events, connections, install = dividend_trace
    before = snapshot(db, real_connect)
    install()
    code = corporate.merge_into_dividends.__code__
    codes = [code, *(constant for constant in code.co_consts if isinstance(constant, type(code)))]
    operations = {
        owner_code: {instruction.offset: instruction.opname for instruction in dis.get_instructions(owner_code)}
        for owner_code in codes
    }

    def trace(frame, event, arg):
        if frame.f_code in operations:
            frame.f_trace_opcodes = True
            if event == "opcode" and operations[frame.f_code].get(frame.f_lasti) == "BUILD_TUPLE":
                assert not state["held"], "dividend parameter tuple constructed under flock"
            return trace
        return None

    previous_trace = sys.gettrace()
    try:
        sys.settrace(trace)
        assert corporate.merge_into_dividends(db, [DIVIDEND_ROW, DIVIDEND_REVISION]) == 2
    finally:
        sys.settrace(previous_trace)
    assert events == dividend_transaction_events(inserts=2)
    assert snapshot(db, real_connect) == {
        **before, "dividends": [DIVIDEND_STORED, DIVIDEND_REVISION_STORED],
    }
    assert_closed(connections)
    assert_unlocked(db, state)


# Task 5: adapters exercise real owners; only external broker I/O is offline.
class OfflineBroker:
    def __init__(self, *, shares=None, candles=None):
        self.shares = [BROKER_ROW] if shares is None else shares
        self.candles = candles or {}
        self.candle_calls = []
        self.dividend_calls = []
        self.closed = False
        self.active = set()

    async def get_shares(self):
        assert not self.closed
        return self.shares

    async def get_bonds(self):
        return []

    async def get_etfs(self):
        return []

    async def get_futures(self):
        raise AssertionError("non-tradeable-fetch")

    async def get_options(self):
        raise AssertionError("non-tradeable-fetch")

    async def get_candles(self, *, figi, date_from, date_to, interval):
        assert not self.closed
        assert interval == "CANDLE_INTERVAL_DAY"
        self.candle_calls.append((figi, date_from, date_to, interval))
        self.active.add(asyncio.current_task())
        try:
            await asyncio.sleep(0)
            assert not self.closed
            return [row for row in self.candles.get(figi, [])
                    if date_from <= date.fromisoformat(row["ts"]) <= date_to]
        finally:
            self.active.remove(asyncio.current_task())

    async def get_dividends(self, figi, from_, to):
        assert not self.closed and from_ <= to
        self.dividend_calls.append(figi)
        return [{"ex_date": "2024-06-15", "amount_per_share": 10.0, "currency": "rub"}]

    async def __aenter__(self):
        assert not self.closed
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        assert not self.active, "client closed before started broker jobs settled"
        self.closed = True


class TestLog:
    __test__ = False

    def __init__(self):
        self.rows = []

    def info(self, event, **fields):
        self.rows.append((event, fields))

    warning = warn = error = info


@pytest.fixture
def adapter_env(coordinated_db, monkeypatch):
    db, real_connect = coordinated_db
    worker = importlib.import_module("worker")
    dividends = importlib.import_module("algotrader_api.scripts_import.import_dividends_tinkoff")
    log, busy, lock_entries = TestLog(), [], []

    @contextmanager
    def acquire(path, *, role, phase, **kwargs):
        try:
            with locks.writer_lock(path, role=role, phase=phase, timeout_seconds=0.03):
                lock_entries.append((role, phase))
                yield
        except locks.WriterLockBusy as exc:
            busy.append(exc)
            raise

    for module in (universe, backfill, corporate, worker):
        monkeypatch.setattr(module, "writer_lock", acquire)
    monkeypatch.setattr(worker, "setup_logging", lambda **kwargs: None)
    monkeypatch.setattr(worker, "heartbeat_loop", lambda *args: None)
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(
        sqlite_path=db, log_level="INFO", history_years=1,
    ))
    monkeypatch.setattr(worker, "logger", log)
    monkeypatch.setattr(backfill, "logger", log)
    monkeypatch.setattr(dividends, "_LOG", log)
    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    monkeypatch.setattr(dividends._rl, "_GLOBAL", dividends._rl.RateLimiter())

    class FixedAdjustmentTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_NOW

    monkeypatch.setattr(forward_adjustment, "datetime", FixedAdjustmentTime)
    return SimpleNamespace(db=db, real_connect=real_connect, worker=worker,
                           dividends=dividends, log=log, busy=busy,
                           acquire=acquire, lock_entries=lock_entries)


@contextmanager
def adapter_contention(env, monkeypatch, kind):
    if kind == "flock":
        with open(locks.writer_lock_path(env.db), "a+b") as descriptor:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        return
    assert kind == "sqlite"
    # Real SQLite writer deliberately bypasses flock. Timeout is test-only.
    blocker = env.real_connect(env.db)
    blocker.execute("BEGIN IMMEDIATE")

    def connect(database, *args, **kwargs):
        kwargs["timeout"] = 0.03
        return env.real_connect(database, *args, **kwargs)

    try:
        with monkeypatch.context() as patcher:
            patcher.setattr(sqlite3, "connect", connect)
            yield
    finally:
        blocker.rollback()
        blocker.close()


def assert_adapter_defer(detail, exc, env, role, phase, kind):
    reason, timeout = ("sqlite-busy", 30.0) if kind == "sqlite" else ("flock-timeout", 0.03)
    assert_busy(exc, env.db, role, phase, reason=reason, timeout=timeout)
    assert detail == locks.format_busy_defer(exc)
    assert detail.startswith(f"DEFER writer-lock-busy role={role} phase={phase} ")
    assert len(detail.splitlines()) == 1
    for field in ("pid=", "database_path=", "lock_path=", "timeout=", "reason=", "result=deferred"):
        assert field in detail
    assert "RAW-FIXTURE-PAYLOAD" not in detail
    if kind == "sqlite":
        assert exc.__cause__.sqlite_errorcode == sqlite3.SQLITE_BUSY


@pytest.mark.parametrize("phase,role,owner_phase", [
    ("universe_sync", "universe-sync", "instruments"),
    ("corporate_actions", "corporate-actions", "corporate-actions"),
    ("dividends", "dividends", "dividends"),
])
@pytest.mark.parametrize("kind", ["flock", "sqlite"])
def test_daily_actual_reference_step_defer_and_retry(
    corporate_db, adapter_env, monkeypatch, phase, role, owner_phase, kind,
):
    env = adapter_env
    clients = []

    def make_client(*, sqlite_path):
        assert sqlite_path == env.db
        monkeypatch.setattr(env.dividends._rl, "_GLOBAL", env.dividends._rl.RateLimiter())
        broker = OfflineBroker()
        clients.append(broker)
        return broker

    monkeypatch.setattr(env.worker.client_mod, "make_client", make_client)
    step = getattr(env.worker, f"_step_{phase}")
    before = snapshot(env.db, env.real_connect)
    with adapter_contention(env, monkeypatch, kind):
        ok, detail = step(env.db)
    assert ok is False
    assert len(env.busy) == 1
    assert_adapter_defer(detail, env.busy[0], env, role, owner_phase, kind)
    assert snapshot(env.db, env.real_connect) == before
    if phase == "dividends":
        assert clients[0].closed
        assert clients[0].dividend_calls == ["FCOORD"]
    assert step(env.db)[0] is True
    saved = snapshot(env.db, env.real_connect)
    assert step(env.db)[0] is True  # zero new rows / existing PKs are success
    repeated = snapshot(env.db, env.real_connect)
    assert repeated == saved
    assert saved["bars"] == before["bars"]
    if phase == "corporate_actions":
        assert_adjustment_saved(env.db, env.real_connect)
    elif phase == "dividends":
        assert len(saved["dividends"]) == 1
    else:
        assert saved["instruments"][0][3] == "Updated"


def load_derivation_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "derive_splits.py"
    spec = importlib.util.spec_from_file_location("task5_derive_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("kind", ["flock", "sqlite"])
def test_actual_derivation_cli_busy_rc75_and_retry(
    corporate_db, adapter_env, monkeypatch, capsys, kind,
):
    env = adapter_env
    cli = load_derivation_cli()
    monkeypatch.setattr(sys, "argv", ["derive_splits.py", env.db, "--no-face-value"])
    before = snapshot(env.db, env.real_connect)
    with adapter_contention(env, monkeypatch, kind):
        rc = cli.main()
    assert rc == 75
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == locks.format_busy_defer(env.busy[0]) + "\n"
    assert_adapter_defer(captured.err.rstrip("\n"), env.busy[0], env,
                         "corporate-actions", "corporate-actions", kind)
    assert snapshot(env.db, env.real_connect) == before
    assert cli.main() == 0
    assert capsys.readouterr().out == "Wrote 1 split rows.\n"
    saved = snapshot(env.db, env.real_connect)
    assert cli.main() == 0
    assert capsys.readouterr().out == "Wrote 0 split rows.\n"
    assert snapshot(env.db, env.real_connect) == saved


@pytest.mark.parametrize("kind", ["flock", "sqlite"])
def test_actual_dividend_cli_busy_rc75_and_retry(adapter_env, monkeypatch, capsys, kind):
    from algotrader_api.ingestion import real_client

    env = adapter_env
    con = env.real_connect(env.db)
    try:
        con.execute("INSERT OR REPLACE INTO secrets (key, value) VALUES ('broker_token', 'offline-test-sentinel')")
        con.execute("INSERT INTO dividends_throttle_pending VALUES ('FCOORD', 'first', 'last', 3)")
        con.commit()
    finally:
        con.close()
    clients = []

    def make_client(*, token):
        assert token == "offline-test-sentinel"
        monkeypatch.setattr(env.dividends._rl, "_GLOBAL", env.dividends._rl.RateLimiter())
        broker = OfflineBroker()
        clients.append(broker)
        return broker

    monkeypatch.setattr(real_client, "RealTinkoffClient", make_client)
    monkeypatch.setattr(sys, "argv", ["import_dividends_tinkoff", env.db])
    before = snapshot(env.db, env.real_connect)
    with adapter_contention(env, monkeypatch, kind):
        rc = env.dividends.main()
    assert rc == 75
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == locks.format_busy_defer(env.busy[0]) + "\n"
    assert_adapter_defer(captured.err.rstrip("\n"), env.busy[0], env, "dividends", "dividends", kind)
    assert snapshot(env.db, env.real_connect) == before
    assert clients[0].closed
    con = env.real_connect(env.db)
    try:
        assert con.execute("SELECT * FROM dividends_throttle_pending").fetchall() == [
            ("FCOORD", "first", "last", 3),
        ]  # failed merge must not reach successful dequeue
    finally:
        con.close()
    assert env.dividends.main() == 0
    assert capsys.readouterr().out == "Wrote 1 dividend rows\n"
    saved = snapshot(env.db, env.real_connect)
    assert env.dividends.main() == 0
    assert capsys.readouterr().out == "Wrote 0 dividend rows\n"
    assert snapshot(env.db, env.real_connect) == saved
    con = env.real_connect(env.db)
    try:
        assert con.execute("SELECT * FROM dividends_throttle_pending").fetchall() == []
    finally:
        con.close()
    assert all(client.closed for client in clients)


@pytest.fixture
def pending_runner(adapter_env, monkeypatch):
    env = adapter_env
    con = env.real_connect(env.db)
    try:
        con.execute("INSERT INTO instrument_metadata "
                    "(figi, last_bar_ts, total_bars, last_run_status) "
                    "VALUES ('FCOORD', '2024-05-15', 1, 'pending')")
        con.commit()
    finally:
        con.close()

    class FrozenRunnerDate(date):
        @classmethod
        def today(cls):
            return date(2024, 5, 17)

    monkeypatch.setattr(backfill, "date", FrozenRunnerDate)
    return env


@contextmanager
def metadata_kernel_contention(env, monkeypatch, phase):
    # Arm a real independent flock only when the selected metadata owner is reached.
    with open(locks.writer_lock_path(env.db), "a+b") as descriptor:
        def acquire(path, *, role, phase: str, **kwargs):
            if role == "backfill-metadata" and phase == target_phase:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return env.acquire(path, role=role, phase=phase, **kwargs)

        target_phase = phase
        with monkeypatch.context() as patcher:
            patcher.setattr(backfill, "writer_lock", acquire)
            yield


def adapter_injected_busy(env, *, role="backfill-metadata", phase="metadata"):
    exc = locks.WriterLockBusy(role=role, phase=phase, database_path=env.db,
                              lock_path=str(locks.writer_lock_path(env.db)),
                              timeout_seconds=0.03, reason="flock-timeout")
    # Adapter injection, not live contention. Raw failure payload must not leak.
    exc.args = ("RAW-FIXTURE-PAYLOAD",)
    return exc


def collect_runner(env, client):
    events = []

    async def collect(event):
        events.append(event)

    return backfill.BackfillRunner(client=client, db_path=env.db, event_sink=collect), events


def assert_run_deferred(runner, events, exc, *, done, total, bars=0):
    assert runner.state == backfill.BackfillState.IDLE
    assert (runner.tickers_done, runner.tickers_total, runner.total_bars) == (done, total, bars)
    assert [event.payload for event in events if event.type == "done"] == [{
        "tickers_done": done, "tickers_total": total, "status": "error",
        **({"total_bars": bars} if total else {}), "detail": locks.format_busy_defer(exc),
    }]


@pytest.mark.parametrize("phase", ["instruments", "metadata"])
@pytest.mark.asyncio
async def test_run_discovery_metadata_busy_propagates(adapter_env, monkeypatch, phase):
    env = adapter_env
    client = OfflineBroker()
    runner, events = collect_runner(env, client)
    before = snapshot(env.db, env.real_connect)
    with metadata_kernel_contention(env, monkeypatch, phase):
        with pytest.raises(locks.WriterLockBusy) as caught:
            await runner.run(history_years=1)
    assert caught.value is env.busy[0]
    assert_adapter_defer(locks.format_busy_defer(caught.value), caught.value, env,
                         "backfill-metadata", phase, "flock")
    assert_run_deferred(runner, events, caught.value, done=0, total=0)
    after = snapshot(env.db, env.real_connect)
    assert after["instrument_metadata"] == before["instrument_metadata"]
    assert after["bars"] == before["bars"]
    # Instrument UPSERT commits before the seed owner in the metadata case.
    assert after["instruments"][0][3] == ("Original" if phase == "instruments" else "Updated")
    await runner.run(history_years=1)
    assert [event.payload["status"] for event in events if event.type == "done"] == ["error", "ok"]
    await client.aclose()


@pytest.mark.asyncio
async def test_run_ticker_metadata_busy_not_done_ok(pending_runner, monkeypatch):
    env = pending_runner
    client = OfflineBroker()
    runner, events = collect_runner(env, client)
    before = snapshot(env.db, env.real_connect)
    with metadata_kernel_contention(env, monkeypatch, "metadata"):
        with pytest.raises(locks.WriterLockBusy) as caught:
            await runner.run(history_years=1, incremental_threshold_days=2,
                             source="tinkoff", limit_to=["FCOORD"])
    assert caught.value is env.busy[0]
    assert client.candle_calls == [("FCOORD", date(2024, 5, 16), date(2024, 5, 17), "CANDLE_INTERVAL_DAY")]
    assert_run_deferred(runner, events, caught.value, done=0, total=1)
    assert snapshot(env.db, env.real_connect) == before
    await runner.run(history_years=1, source="tinkoff", limit_to=["FCOORD"])
    assert events[-1].payload["status"] == "ok"
    assert runner.tickers_done == 1
    await client.aclose()


VALID_CANDLE = {"ts": "2024-05-16", "open": 51.0, "high": 52.0, "low": 49.0,
                "close": 50.0, "volume": 100, "is_complete": True}


@pytest.mark.asyncio
async def test_run_ticker_post_bar_metadata_acquisition_injection(pending_runner, monkeypatch):
    env = pending_runner
    client = OfflineBroker(candles={"FCOORD": [VALID_CANDLE]})
    runner, events = collect_runner(env, client)
    before = snapshot(env.db, env.real_connect)
    exc = adapter_injected_busy(env)
    at_acquisition = []

    def acquire(path, *, role, phase, **kwargs):
        assert (role, phase) == ("backfill-metadata", "metadata")
        at_acquisition.extend(snapshot(env.db, env.real_connect)["instrument_metadata"])
        raise exc  # post-bar-commit metadata acquisition adapter injection

    with monkeypatch.context() as patcher:
        patcher.setattr(backfill, "writer_lock", acquire)
        with pytest.raises(locks.WriterLockBusy) as caught:
            await runner.run(history_years=1, incremental_threshold_days=2,
                             source="tinkoff", limit_to=["FCOORD"])
    assert caught.value is exc
    assert_run_deferred(runner, events, exc, done=0, total=1)
    after = snapshot(env.db, env.real_connect)
    # The real bar writer already commits aggregate/status metadata. The rejected
    # explicit metadata owner must leave that acquisition-time state untouched.
    assert after["instrument_metadata"] == at_acquisition
    assert at_acquisition[0][2] == before["instrument_metadata"][0][2] is None  # last_backfilled_at
    con = env.real_connect(env.db)
    try:
        assert con.execute("SELECT ts, close FROM bars WHERE figi='FCOORD' ORDER BY ts").fetchall() == [
            ("2024-05-15", 50.0), ("2024-05-16", 50.0),
        ]
    finally:
        con.close()
    assert "RAW-FIXTURE-PAYLOAD" not in str([event.payload for event in events])
    assert "RAW-FIXTURE-PAYLOAD" not in str(after["ingestion_logs"])
    await runner.run(history_years=1, source="tinkoff", limit_to=["FCOORD"])
    assert events[-1].payload["status"] == "ok"
    assert snapshot(env.db, env.real_connect)["bars"] == after["bars"]
    metadata = runner._get_metadata("FCOORD")
    assert metadata is not None and metadata["last_run_status"] == "ok"
    await client.aclose()


@pytest.mark.asyncio
async def test_run_metadata_busy_waits_for_other_started_figi(pending_runner, monkeypatch):
    env = pending_runner
    con = env.real_connect(env.db)
    try:
        con.execute("INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
                    "VALUES ('SETTLE', 'FSETTLE', 'share', 'Settle', 'RUB', 1)")
        con.execute("INSERT INTO instrument_metadata "
                    "(figi, last_bar_ts, total_bars, last_run_status) "
                    "VALUES ('FSETTLE', '2024-05-15', 1, 'pending')")
        con.commit()
    finally:
        con.close()
    started, failed = asyncio.Event(), asyncio.Event()

    class WaitingBroker(OfflineBroker):
        async def get_candles(self, *, figi, date_from, date_to, interval):
            assert not self.closed and interval == "CANDLE_INTERVAL_DAY"
            assert (date_from, date_to) == (date(2024, 5, 16), date(2024, 5, 17))
            task = asyncio.current_task()
            self.active.add(task)
            self.candle_calls.append((figi, date_from, date_to, interval))
            try:
                if figi == "FCOORD":
                    await asyncio.wait_for(started.wait(), timeout=1)
                else:
                    assert figi == "FSETTLE"
                    started.set()
                    await asyncio.wait_for(failed.wait(), timeout=1)
                    await asyncio.sleep(0)
                assert not self.closed
                return [VALID_CANDLE]
            finally:
                self.active.remove(task)

    client = WaitingBroker()
    runner, events = collect_runner(env, client)
    before = snapshot(env.db, env.real_connect)
    exc = adapter_injected_busy(env)
    at_acquisition = []

    def acquire(path, *, role, phase, **kwargs):
        if not failed.is_set():
            at_acquisition.extend(row for row in snapshot(env.db, env.real_connect)["instrument_metadata"]
                                  if row[0] == "FCOORD")
            assert client.active, "second broker job must have started before metadata failure"
            failed.set()
            raise exc  # acquisition-only adapter injection after first FIGI's bar commit
        return env.acquire(path, role=role, phase=phase, **kwargs)

    jobs_before = set(asyncio.all_tasks())
    with monkeypatch.context() as patcher:
        patcher.setattr(backfill, "writer_lock", acquire)
        try:
            with pytest.raises(locks.WriterLockBusy) as caught:
                await asyncio.wait_for(runner.run(history_years=1, source="tinkoff",
                    limit_to=["FCOORD", "FSETTLE"]), timeout=2)
        finally:
            await client.aclose()
    assert caught.value is exc
    assert client.closed and not client.active
    assert not (set(asyncio.all_tasks()) - jobs_before), "runner left background jobs pending"
    assert_run_deferred(runner, events, exc, done=1, total=2, bars=1)
    after = snapshot(env.db, env.real_connect)
    assert [row for row in after["instrument_metadata"] if row[0] == "FCOORD"] == at_acquisition
    assert at_acquisition[0][2] == before["instrument_metadata"][0][2] is None
    metadata = runner._get_metadata("FSETTLE")
    assert metadata is not None and metadata["last_run_status"] == "ok"
    con = env.real_connect(env.db)
    try:
        assert con.execute("SELECT figi, ts, close FROM bars ORDER BY figi, ts").fetchall() == [
            ("FCOORD", "2024-05-15", 50.0), ("FCOORD", "2024-05-16", 50.0),
            ("FSETTLE", "2024-05-16", 50.0),
        ]
    finally:
        con.close()
    runner.client = OfflineBroker(candles={"FCOORD": [VALID_CANDLE], "FSETTLE": [VALID_CANDLE]})
    await runner.run(history_years=1, source="tinkoff", limit_to=["FCOORD", "FSETTLE"])
    assert events[-1].payload["status"] == "ok" and runner.tickers_done == 2
    assert snapshot(env.db, env.real_connect)["bars"] == after["bars"]
    await runner.client.aclose()


@pytest.mark.parametrize("mode", ["scheduled", "manual"])
@pytest.mark.parametrize("case", ["discovery-instruments", "discovery-seed", "ticker-empty", "ticker-bars"])
@pytest.mark.asyncio
async def test_run_worker_metadata_busy_not_pipeline_ok(pending_runner, monkeypatch, mode, case):
    env = pending_runner
    clients = []

    def make_client(*, sqlite_path):
        assert sqlite_path == env.db
        client = OfflineBroker(candles={"FCOORD": [VALID_CANDLE]} if case == "ticker-bars" else {})
        clients.append(client)
        return client

    monkeypatch.setattr(env.worker.client_mod, "make_client", make_client)
    # Legacy run_worker token check references a removed client symbol; isolate that boundary only.
    monkeypatch.setattr(env.worker.client_mod, "read_token_file",
                        lambda: "offline-test-sentinel", raising=False)
    before = snapshot(env.db, env.real_connect)
    at_acquisition = []
    if case == "ticker-bars":
        exc = adapter_injected_busy(env)

        def acquire(path, *, role, phase, **kwargs):
            con = env.real_connect(path)
            try:
                committed = con.execute("SELECT 1 FROM bars WHERE figi='FCOORD' AND ts='2024-05-16'").fetchone()
            finally:
                con.close()
            if phase == "metadata" and committed:
                at_acquisition.extend(snapshot(env.db, env.real_connect)["instrument_metadata"])
                env.busy.append(exc)
                raise exc  # adapter injection at metadata acquisition after real bars commit
            return env.acquire(path, role=role, phase=phase, **kwargs)

        with monkeypatch.context() as patcher:
            patcher.setattr(backfill, "writer_lock", acquire)
            rc = await env.worker.run_worker(mode)
    else:
        phase = "instruments" if case == "discovery-instruments" else "metadata"
        # For per-ticker empty response, seed metadata must first succeed.
        if case == "ticker-empty":
            with open(locks.writer_lock_path(env.db), "a+b") as descriptor:
                def acquire(path, *, role, phase, **kwargs):
                    if phase == "metadata" and clients and clients[0].candle_calls:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return env.acquire(path, role=role, phase=phase, **kwargs)

                with monkeypatch.context() as patcher:
                    patcher.setattr(backfill, "writer_lock", acquire)
                    rc = await env.worker.run_worker(mode)
        else:
            with metadata_kernel_contention(env, monkeypatch, phase):
                rc = await env.worker.run_worker(mode)
        assert len(env.busy) == 1
        exc = env.busy[0]
    assert rc == 2
    assert clients[0].closed and not clients[0].active
    con = env.real_connect(env.db)
    try:
        rows = con.execute("SELECT phase, status, rows_processed, detail, finished_at FROM pipeline").fetchall()
        assert len(rows) == 1
        assert rows[0][:4] == ("fetch_universe_bars", "err", 0, locks.format_busy_defer(exc))
        assert rows[0][4] is not None
    finally:
        con.close()
    assert [fields["error"] for event, fields in env.log.rows if event == "worker.run.failed"] == [
        locks.format_busy_defer(exc),
    ]
    assert snapshot(env.db, env.real_connect)["instrument_metadata"] == (
        at_acquisition if case == "ticker-bars" else before["instrument_metadata"]
    )
    if case.startswith("ticker"):
        assert clients[0].candle_calls, "actual Tinkoff candle path must reach metadata"
    if case == "ticker-bars":
        con = env.real_connect(env.db)
        try:
            assert con.execute("SELECT ts, close FROM bars ORDER BY ts").fetchall() == [
                ("2024-05-15", 50.0), ("2024-05-16", 50.0),
            ]
        finally:
            con.close()
    assert "RAW-FIXTURE-PAYLOAD" not in str(env.log.rows)
    assert await env.worker.run_worker(mode) == 0
    assert all(client.closed for client in clients)
    con = env.real_connect(env.db)
    try:
        assert con.execute("SELECT status FROM pipeline ORDER BY id").fetchall() == [("err",), ("ok",)]
    finally:
        con.close()


@pytest.fixture
def gap_env(adapter_env, monkeypatch):
    env = adapter_env
    con = env.real_connect(env.db)
    try:
        con.execute("INSERT INTO instrument_metadata "
                    "(figi, last_bar_ts, total_bars, last_run_status) "
                    "VALUES ('FCOORD', '2024-05-15', 1, 'pending')")
        con.commit()
    finally:
        con.close()

    class FrozenWorkerDate(date):
        @classmethod
        def today(cls):
            return date(2024, 5, 17)

    # Only the worker's calendar is frozen; keep actual runner dates/signatures.
    monkeypatch.setattr(env.worker, "date", FrozenWorkerDate)
    return env


@pytest.mark.parametrize("historical", [False, True], ids=["trailing", "historical"])
def test_gap_recovery_metadata_busy_not_zero_success(gap_env, monkeypatch, historical):
    from algotrader_api.data_quality.gap_recovery import find_gaps, recover_gaps

    env = gap_env
    if historical:
        con = env.real_connect(env.db)
        try:
            con.execute("INSERT INTO bars (figi, ts, open, high, low, close, volume) "
                        "VALUES ('FCOORD', '2024-05-13', 50, 50, 50, 50, 100)")
            con.commit()
        finally:
            con.close()
    clients = []

    def make_client(*, sqlite_path):
        assert sqlite_path == env.db
        broker = OfflineBroker()
        clients.append(broker)
        return broker

    monkeypatch.setattr(env.worker.client_mod, "make_client", make_client)
    before = snapshot(env.db, env.real_connect)
    with metadata_kernel_contention(env, monkeypatch, "metadata"):
        ok, detail = env.worker._step_gap_recovery(env.db)
    assert ok is False
    assert len(env.busy) == 1
    assert_adapter_defer(detail, env.busy[0], env, "backfill-metadata", "metadata", "flock")
    expected_range = (date(2024, 5, 14), date(2024, 5, 14)) if historical else (
        date(2024, 5, 16), date(2024, 5, 17))
    assert clients[0].candle_calls == [("FCOORD", *expected_range, "CANDLE_INTERVAL_DAY")]
    assert clients[0].closed and not clients[0].active
    assert snapshot(env.db, env.real_connect) == before
    if historical:
        # Also call the real historical helper directly; it already propagates metadata BUSY.
        runner, _ = collect_runner(env, OfflineBroker())
        with metadata_kernel_contention(env, monkeypatch, "metadata"):
            with pytest.raises(locks.WriterLockBusy) as caught:
                asyncio.run(recover_gaps(env.db, runner, find_gaps(env.db)))
        assert caught.value is env.busy[-1]
        assert runner.client.candle_calls == [("FCOORD", *expected_range, "CANDLE_INTERVAL_DAY")]
        asyncio.run(runner.client.aclose())
    assert env.worker._step_gap_recovery(env.db)[0] is True
    assert clients[-1].closed
    assert snapshot(env.db, env.real_connect)["bars"] == before["bars"]


@pytest.mark.parametrize("historical", [False, True], ids=["trailing", "historical"])
@pytest.mark.parametrize("error_kind", ["other-writer-busy", "ordinary-error"])
def test_gap_metadata_adapters_preserve_other_error_policy(gap_env, monkeypatch, historical, error_kind):
    env = gap_env
    if historical:
        con = env.real_connect(env.db)
        try:
            con.execute("INSERT INTO bars (figi, ts, open, high, low, close, volume) "
                        "VALUES ('FCOORD', '2024-05-13', 50, 50, 50, 50, 100)")
            con.commit()
        finally:
            con.close()
    exc = (adapter_injected_busy(env, role="bar-writer", phase="bars")
           if error_kind == "other-writer-busy" else RuntimeError("ordinary-owner-error"))
    if error_kind == "other-writer-busy":
        exc.args = ("other-role-owner-error",)

    def acquire(path, *, role, phase, **kwargs):
        assert (role, phase) == ("backfill-metadata", "metadata")
        raise exc  # ordinary/non-metadata adapter injection; retain old handling

    client = OfflineBroker()
    monkeypatch.setattr(env.worker.client_mod, "make_client", lambda *, sqlite_path: client)
    monkeypatch.setattr(backfill, "writer_lock", acquire)
    before = snapshot(env.db, env.real_connect)
    ok, detail = env.worker._step_gap_recovery(env.db)
    if historical:
        assert ok is False and detail == f"gap recovery failed: {exc}"
    else:
        assert ok is True and detail.startswith("gap recovery: 0 bars filled ")
        assert [fields["error"] for event, fields in env.log.rows
                if event == "worker.gap_recovery.fill_failed"] == [str(exc)]
    assert client.candle_calls and client.closed
    assert snapshot(env.db, env.real_connect) == before


@pytest.mark.parametrize("mode", ["scheduled", "manual"])
@pytest.mark.asyncio
async def test_run_worker_propagated_ordinary_error_keeps_generic_rc2(adapter_env, monkeypatch, mode):
    env = adapter_env
    client = OfflineBroker()
    monkeypatch.setattr(env.worker.client_mod, "make_client", lambda *, sqlite_path: client)
    monkeypatch.setattr(env.worker.client_mod, "read_token_file", lambda: "offline-test-sentinel", raising=False)
    exc = RuntimeError("ordinary-run-error")

    class BrokenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            raise exc  # real run/_emit body, ordinary dependency failure before metadata writes

    monkeypatch.setattr(backfill, "datetime", BrokenClock)
    assert await env.worker.run_worker(mode) == 2
    assert client.closed
    con = env.real_connect(env.db)
    try:
        assert con.execute("SELECT phase, status, detail FROM pipeline").fetchall() == [
            ("fetch_universe_bars", "err", "ordinary-run-error"),
        ]
    finally:
        con.close()
    assert [fields["error"] for event, fields in env.log.rows if event == "worker.run.failed"] == [str(exc)]


def test_run_backfill_retains_rc1_for_metadata_busy(adapter_env, monkeypatch):
    from algotrader_api.db import secrets

    env = adapter_env
    client = OfflineBroker()
    monkeypatch.setattr(secrets, "get_broker_token", lambda db_path: "offline-test-sentinel")
    monkeypatch.setattr(env.worker.client_mod, "make_client", lambda *, sqlite_path, use_fake: client)
    try:
        with metadata_kernel_contention(env, monkeypatch, "instruments"):
            assert env.worker.run_backfill() == 1
        assert len(env.busy) == 1
        assert [fields["payload"]["status"] for event, fields in env.log.rows
                if event == "worker.backfill.event" and fields["type"] == "done"] == ["error"]
    finally:
        # The existing synchronous run_backfill owns no client-finally; test owns this fake.
        asyncio.run(client.aclose())


@pytest.mark.parametrize("entrypoint", ["daily", "cli"])
def test_dividend_partial_commit_keeps_prior_figi_and_failed_queue(
    adapter_env, monkeypatch, capsys, entrypoint,
):
    from algotrader_api.ingestion import real_client

    env = adapter_env
    con = env.real_connect(env.db)
    try:
        con.execute("INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
                    "VALUES ('PRIOR', 'FPRIOR', 'share', 'Prior', 'RUB', 1)")
        con.executemany("INSERT INTO dividends_throttle_pending VALUES (?, ?, ?, ?)", [
            ("FPRIOR", "first", "01", 2), ("FCOORD", "first", "02", 3),
        ])
        con.execute("INSERT OR REPLACE INTO secrets (key, value) VALUES ('broker_token', 'offline-test-sentinel')")
        con.commit()
    finally:
        con.close()
    clients = []

    def make_client(**kwargs):
        assert kwargs == ({"sqlite_path": env.db} if entrypoint == "daily" else {"token": "offline-test-sentinel"})
        monkeypatch.setattr(env.dividends._rl, "_GLOBAL", env.dividends._rl.RateLimiter())
        client = OfflineBroker()
        clients.append(client)
        return client

    monkeypatch.setattr(env.worker.client_mod, "make_client", make_client)
    monkeypatch.setattr(real_client, "RealTinkoffClient", make_client)
    monkeypatch.setattr(sys, "argv", ["import_dividends_tinkoff", env.db])
    blocker = env.real_connect(env.db)
    calls = []

    def acquire(path, *, role, phase, **kwargs):
        assert (role, phase) == ("dividends", "dividends")
        calls.append((role, phase))
        if len(calls) == 2:
            # Real numeric BUSY on second FIGI; the first FIGI merge/dequeue has committed.
            blocker.execute("BEGIN IMMEDIATE")
        return env.acquire(path, role=role, phase=phase, **kwargs)

    def connect(database, *args, **kwargs):
        kwargs["timeout"] = 0.03
        return env.real_connect(database, *args, **kwargs)

    def invoke():
        return env.worker._step_dividends(env.db) if entrypoint == "daily" else env.dividends.main()

    try:
        with monkeypatch.context() as patcher:
            patcher.setattr(corporate, "writer_lock", acquire)
            patcher.setattr(sqlite3, "connect", connect)
            outcome = invoke()
    finally:
        blocker.rollback()
        blocker.close()
    assert len(env.busy) == 1
    detail = locks.format_busy_defer(env.busy[0])
    assert_adapter_defer(detail, env.busy[0], env, "dividends", "dividends", "sqlite")
    assert outcome == ((False, detail) if entrypoint == "daily" else 75)
    captured = capsys.readouterr()
    assert captured.err == ("" if entrypoint == "daily" else detail + "\n")
    assert captured.out == ""
    assert clients[0].closed and clients[0].dividend_calls == ["FPRIOR", "FCOORD"]
    con = env.real_connect(env.db)
    try:
        prior = con.execute("SELECT * FROM dividends WHERE figi='FPRIOR'").fetchall()
        assert len(prior) == 1
        assert con.execute("SELECT figi, amount_per_share FROM dividends").fetchall() == [("FPRIOR", 10.0)]
        assert con.execute("SELECT * FROM dividends_throttle_pending").fetchall() == [
            ("FCOORD", "first", "02", 3),
        ]
    finally:
        con.close()
    assert invoke() == ((True, "dividends: tinkoff=1 queued=0") if entrypoint == "daily" else 0)
    capsys.readouterr()
    con = env.real_connect(env.db)
    try:
        assert con.execute("SELECT * FROM dividends WHERE figi='FPRIOR'").fetchall() == prior
        assert con.execute("SELECT figi, amount_per_share FROM dividends ORDER BY figi").fetchall() == [
            ("FCOORD", 10.0), ("FPRIOR", 10.0),
        ]
        assert con.execute("SELECT * FROM dividends_throttle_pending").fetchall() == []
    finally:
        con.close()
    assert invoke() == ((True, "dividends: tinkoff=0 queued=0") if entrypoint == "daily" else 0)
    assert all(client.closed for client in clients)
