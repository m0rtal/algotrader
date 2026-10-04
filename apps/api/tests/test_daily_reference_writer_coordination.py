"""Real daily transaction owners: order, contention, cleanup and retry state."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import socket
import sqlite3
import time

import pytest

from algotrader_api.db import sqlite as sqlitedb
from algotrader_api.ingestion import backfill, universe
from algotrader_api.ingestion import writer_lock as locks
from algotrader_api.ingestion.universe_sync import run_universe_sync


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
                       fail_operation=None, failure_at_commit=1):
    class Tracked(sqlite3.Connection):
        _injected_rollback_failure = False
        _commits = 0
        settings_at_begin = None

        def execute(self, sql, parameters=()):
            operation = sql.lstrip().split(None, 1)[0].upper()
            if operation in {"BEGIN", "INSERT", "UPDATE", "DELETE", "REPLACE"}:
                assert state["held"], sql
                events.append((operation,))
            if operation == "BEGIN":
                self.settings_at_begin = tuple(
                    super(Tracked, self).execute(f"PRAGMA {name}").fetchone()[0]
                    for name in ("foreign_keys", "busy_timeout", "journal_mode")
                )
            if failure is not None and operation == fail_operation:
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
            for table in ("instruments", "instrument_metadata", "bars", "ingestion_logs")
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
    assert events == [("acquire", "backfill-metadata", "instruments"), ("BEGIN",), ("INSERT",),
                      ("commit",), ("release", "backfill-metadata", "instruments"), ("close",)]
