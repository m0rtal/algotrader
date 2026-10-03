"""Tests for the per-FIGI broker client lifecycle in
``_async_backfill_impl`` (apps/api/src/algotrader_api/ingestion/backfill.py).

Production bug (2026-10-03 worker smoke run):
    ``_async_backfill_impl`` resolved the canonical ``sqlite_path`` and
    forwarded it to ``make_client(sqlite_path=...)`` once per loop
    iteration — but it never called ``aclose()`` on the returned
    client. Across 74 bond figis (one chain step) the worker leaked
    74 gRPC channels and their backing HTTP/2 connections; the
    sandbox's connection-tracking surface maxed out and the daily
    cycle stalled.

Fix contract (the test file is the spec):
    * Per-FIGI factory client MUST be closed exactly once, AFTER the
      awaited ``get_candles`` returns, BEFORE the writer-lock
      protected bar transaction runs. The try/finally is tight:
      ``rl.acquire("get_candles")`` and ``client.get_candles(...)``
      sit inside it; the prefilter computation and the
      ``replace_bars_for_figi_with_rowcount`` call sit OUTSIDE.
    * If ``make_client`` itself raises, no ``aclose`` is called
      (there is no client to close).
    * If a figi is skipped (current bars >= target_days), no client
      is created and no ``aclose`` is called.
    * A failing ``aclose`` must NOT mask the fetch error NOR lose
      the fetched candles.
    * Cancellation: closing a client inside a cancelled fetch
      context must not raise (the SDK's ``aclose`` is best-effort).
    * Order vs writer lock: aclose is called BEFORE
      ``replace_bars_for_figi_with_rowcount`` is called (the writer
      lock is acquired INSIDE that function).
    * No raw secrets land in the log on close failure.

The tests use a FakeAsyncTinkoffClient (no real network, no SDK
import). The factory, rate limiter, and writer are all patched via
monkeypatch so the assertions are hermetic and independent of the
real gRPC stack.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# pytest is imported transitively via conftest; keep the explicit
# import so the test file can be run standalone with pytest.
import pytest  # noqa: F401  (kept for IDE / direct pytest invocation)


# ─── shared DB fixture ──────────────────────────────────────────────
#
# The bar writer (``replace_bars_for_figi_with_rowcount``) needs a
# file-backed SQLite (the shared ``<db>.writer.lock`` and WAL depend
# on it). The fixture seeds the minimum schema the loop touches:
# ``instruments``, ``bars``, ``instrument_metadata``.


def _make_db(tmp_path):
    db_file = str(tmp_path / "bonds.db")
    con = sqlite3.connect(db_file)
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY, ticker TEXT, class TEXT
        );
        CREATE TABLE bars (
            figi TEXT, ts TEXT,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            source TEXT DEFAULT 'moex',
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE instrument_metadata (
            figi              TEXT PRIMARY KEY,
            last_bar_ts       TEXT,
            first_bar_ts      TEXT,
            last_backfilled_at TEXT,
            total_bars        INTEGER NOT NULL DEFAULT 0,
            last_run_status   TEXT,
            last_run_at       TEXT,
            last_error        TEXT
        );
    """)
    con.commit()
    return db_file, con


def _make_candle(figi: str, ts: str):
    return SimpleNamespace(
        figi=figi, ts=ts, open=100, high=101, low=99,
        close=100, volume=1000,
    )


def _seed_bond(con, figi: str = "BBG000BOND00", bars: int = 0):
    """Insert one bond with ``bars`` synthetic rows (or zero)."""
    con.execute(
        "INSERT INTO instruments VALUES (?, ?, 'bond')",
        (figi, "BOND00"),
    )
    for i in range(bars):
        con.execute(
            "INSERT INTO bars VALUES (?, ?, 100, 101, 99, 100, 1000, 'moex')",
            (figi, f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"),
        )
    con.commit()


def _close_events(client: "FakeAsyncTinkoffClient"):
    """Return the list of (event, payload) tuples the fake recorded.

    The events the lifecycle cares about are ``aclose_start`` /
    ``aclose_done`` and ``get_candles_start`` / ``get_candles_done``.
    Insertion order matches wall-clock order on the fake's
    in-process event log.
    """
    return list(client.events)


# ─── fake client ────────────────────────────────────────────────────
#
# Mimics the real Tinkoff Protocol's surface. ``aclose`` semantics are
# bounded: a single ``aclose_done`` event, swallowing raised errors
# (per the real client's best-effort close), so a test can
# deliberately inject a faulty ``aclose`` without corrupting the
# event log.


class FakeAsyncTinkoffClient:
    """Fake async Tinkoff client.

    Each instance records ordered events to ``self.events``:
      * "get_candles_start"
      * "get_candles_done"
      * "aclose_start"
      * "aclose_done"
    so tests can assert on ORDER. ``aclose_fail_with`` lets a test
    inject a faulty close path; ``fetch_fail_with`` lets a test
    inject a faulty fetch. ``cancelled`` lets a test simulate
    cancellation by raising ``asyncio.CancelledError`` from the
    pending ``get_candles`` await.
    """

    def __init__(
        self,
        *,
        candles: list | None = None,
        aclose_fail_with: BaseException | None = None,
        fetch_fail_with: BaseException | None = None,
        cancelled: bool = False,
    ) -> None:
        self.events: list[tuple[str, dict]] = []
        self._candles = list(candles or [])
        self._aclose_fail_with = aclose_fail_with
        self._fetch_fail_with = fetch_fail_with
        self._cancelled = cancelled
        self.aclose_called = 0
        self.fetch_called = 0

    async def get_candles(self, *, figi, date_from, date_to, **_kw):
        self.fetch_called += 1
        self.events.append(("get_candles_start", {"figi": figi}))
        if self._cancelled:
            # Simulate cancellation arriving on the await.
            try:
                raise asyncio.CancelledError()
            finally:
                # Mirror real asyncio: CancelledError semantics in
                # an ``async def`` body go through ``finally``; the
                # event log should still see the start.
                self.events.append(("get_candles_done", {"cancelled": True}))
        if self._fetch_fail_with is not None:
            try:
                raise self._fetch_fail_with
            finally:
                self.events.append(("get_candles_done", {"err": True}))
        # Normal path: yield once so awaiting coroutines actually
        # suspend; without this, fast-path tests pass even when the
        # production code does not await.
        await asyncio.sleep(0)
        self.events.append(("get_candles_done", {"ok": True}))
        return list(self._candles)

    async def aclose(self) -> None:
        self.aclose_called += 1
        self.events.append(("aclose_start", {}))
        if self._aclose_fail_with is not None:
            try:
                raise self._aclose_fail_with
            finally:
                self.events.append(("aclose_done", {"err": True}))
        # Mirror real client: aclose is best-effort. The
        # production code path (RealTinkoffClient.aclose) does not
        # raise on a closed channel; the fake mirrors that.
        await asyncio.sleep(0)
        self.events.append(("aclose_done", {"ok": True}))


def _patch_client_factory(
    monkeypatch, fake: FakeAsyncTinkoffClient | None, *, factory_raises: BaseException | None = None,
):
    """Patch ``algotrader_api.ingestion.client.make_client`` to return
    the given fake.

    The brief forbids changes to the ``make_client`` signature, so
    we accept whatever kwargs the production code forwards and
    return the same instance. The same fake is used for every
    figi; the test does not need per-figi dispatch because the
    bond universe is one figi. ``fake`` may be None when the
    caller wants to inject ``factory_raises`` — the fake itself is
    irrelevant because the factory throws before returning.
    """
    def _factory(**_kwargs):
        if factory_raises is not None:
            raise factory_raises
        assert fake is not None  # invariant: factory_raises is None here
        return fake

    monkeypatch.setattr(
        "algotrader_api.ingestion.client.make_client", _factory,
    )


def _patch_rate_limit(monkeypatch):
    """Stub the global rate limiter so ``rl.acquire`` does not block."""
    fake_rl = MagicMock()
    fake_rl.acquire = AsyncMock()
    monkeypatch.setattr(
        "algotrader_api.ingestion.rate_limit.get_global", lambda: fake_rl,
    )
    return fake_rl


# ─── RED tests (currently failing) ──────────────────────────────────


def test_async_backfill_impl_closes_client_after_successful_fetch(
    tmp_path, monkeypatch,
):
    """Normal path: get_candles returns candles → aclose called
    once, AFTER get_candles_done, BEFORE the writer-lock protected
    bar transaction runs.
    """
    from algotrader_api.ingestion import backfill

    db_file, con = _make_db(tmp_path)
    try:
        _seed_bond(con, figi="BBG000BOND00", bars=0)
        candles = [_make_candle("BBG000BOND00", f"2025-04-{i+1:02d}") for i in range(5)]
        fake = FakeAsyncTinkoffClient(candles=candles)
        _patch_client_factory(monkeypatch, fake)
        _patch_rate_limit(monkeypatch)

        result = asyncio.run(
            backfill._async_backfill_impl(target_days=30, conn=con),
        )
    finally:
        con.close()

    assert fake.aclose_called == 1, (
        f"client must be closed exactly once on the success path; "
        f"got aclose_called={fake.aclose_called}"
    )
    assert result["bars_added"] == 5, (
        f"5 fetched candles must be written; got {result!r}"
    )

    events = [name for name, _ in _close_events(fake)]
    # Order: get_candles_start, get_candles_done, aclose_start,
    # aclose_done. The fetch MUST complete before close starts.
    assert events.index("get_candles_done") < events.index("aclose_start"), (
        f"aclose must run AFTER get_candles returns; got order {events!r}"
    )
    assert events.index("aclose_done") == len(events) - 1, (
        f"aclose must be the LAST event for this figi; got {events!r}"
    )


def test_async_backfill_impl_closes_client_when_fetch_returns_empty(
    tmp_path, monkeypatch,
):
    """Empty-result path: get_candles returns [] → aclose called
    once, the empty branch is taken, the counter is incremented,
    no rows are written.
    """
    from algotrader_api.ingestion import backfill

    db_file, con = _make_db(tmp_path)
    try:
        _seed_bond(con, figi="BBG000BOND00", bars=0)
        fake = FakeAsyncTinkoffClient(candles=[])
        _patch_client_factory(monkeypatch, fake)
        _patch_rate_limit(monkeypatch)

        result = asyncio.run(
            backfill._async_backfill_impl(target_days=30, conn=con),
        )
    finally:
        con.close()

    assert fake.aclose_called == 1, (
        f"client must be closed once on the empty-fetch path; "
        f"got aclose_called={fake.aclose_called}"
    )
    assert result["bars_added"] == 0
    assert result["figis_processed"] == 1


def test_async_backfill_impl_closes_client_on_fetch_exception(
    tmp_path, monkeypatch,
):
    """Exception path: get_candles raises → aclose called once,
    the error counter increments, the loop continues. The next
    figi (none in this test) would be processed normally; here
    the assertion is just that the close ran.
    """
    from algotrader_api.ingestion import backfill

    db_file, con = _make_db(tmp_path)
    try:
        _seed_bond(con, figi="BBG000BOND00", bars=0)
        fake = FakeAsyncTinkoffClient(
            fetch_fail_with=RuntimeError("tinkoff boom"),
        )
        _patch_client_factory(monkeypatch, fake)
        _patch_rate_limit(monkeypatch)

        result = asyncio.run(
            backfill._async_backfill_impl(target_days=30, conn=con),
        )
    finally:
        con.close()

    assert fake.aclose_called == 1, (
        f"client must be closed even when fetch raises; "
        f"got aclose_called={fake.aclose_called}"
    )
    assert result["errors"] == 1, (
        f"fetch error must increment errors; got {result!r}"
    )
    # Fetch was actually attempted, then close ran.
    events = [name for name, _ in _close_events(fake)]
    assert "get_candles_start" in events
    assert "aclose_start" in events
    assert events.index("aclose_start") > events.index("get_candles_start")


def test_async_backfill_impl_closes_client_on_cancellation(
    tmp_path, monkeypatch,
):
    """Cancellation path: get_candles awaits a cancelled
    coroutine → aclose called once, the cancellation surfaces
    (and ``asyncio.run`` re-raises it). The test catches the
    CancelledError so it can assert on the post-cancel state —
    the close MUST have run in the ``finally`` block before the
    cancellation propagated to the caller.
    """
    from algotrader_api.ingestion import backfill

    db_file, con = _make_db(tmp_path)
    try:
        _seed_bond(con, figi="BBG000BOND00", bars=0)
        fake = FakeAsyncTinkoffClient(cancelled=True)
        _patch_client_factory(monkeypatch, fake)
        _patch_rate_limit(monkeypatch)

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                backfill._async_backfill_impl(target_days=30, conn=con),
            )
    finally:
        con.close()

    assert fake.aclose_called == 1, (
        f"client must be closed even on cancellation; "
        f"got aclose_called={fake.aclose_called}"
    )
    # The fetch was started, and the close ran in the finally
    # block AFTER the cancel surfaced.
    events = [name for name, _ in _close_events(fake)]
    assert "get_candles_start" in events
    assert "aclose_start" in events
    assert events.index("aclose_start") > events.index("get_candles_start")


def test_async_backfill_impl_does_not_call_close_when_factory_fails(
    tmp_path, monkeypatch,
):
    """Factory-failure path: ``make_client`` itself raises
    (e.g. broker token missing) → no client exists, so no
    ``aclose`` is called. The error counter increments.
    """
    from algotrader_api.ingestion import backfill

    db_file, con = _make_db(tmp_path)
    try:
        _seed_bond(con, figi="BBG000BOND00", bars=0)
        _patch_client_factory(
            monkeypatch, fake=None,
            factory_raises=RuntimeError("no token"),
        )
        _patch_rate_limit(monkeypatch)

        result = asyncio.run(
            backfill._async_backfill_impl(target_days=30, conn=con),
        )
    finally:
        con.close()

    assert result["errors"] == 1, (
        f"factory failure must increment errors; got {result!r}"
    )
    # No fake was created; the test is a pass if we did not crash.


def test_async_backfill_impl_does_not_create_client_for_skipped_figi(
    tmp_path, monkeypatch,
):
    """Skip path: a figi with >= target_days bars is skipped
    BEFORE the factory is called → ``make_client`` never runs
    for that figi. We verify the factory was NOT called.
    """
    from algotrader_api.ingestion import backfill

    db_file, con = _make_db(tmp_path)
    try:
        _seed_bond(con, figi="BBG000BOND00", bars=30)  # 30 == target
        factory_call_count = {"n": 0}

        def _factory(**_kwargs):
            factory_call_count["n"] += 1
            return FakeAsyncTinkoffClient(candles=[])

        monkeypatch.setattr(
            "algotrader_api.ingestion.client.make_client", _factory,
        )
        _patch_rate_limit(monkeypatch)

        result = asyncio.run(
            backfill._async_backfill_impl(target_days=30, conn=con),
        )
    finally:
        con.close()

    assert result["skipped"] == 1
    assert factory_call_count["n"] == 0, (
        f"factory must NOT be called for skipped figis; "
        f"got {factory_call_count['n']} calls"
    )


def test_async_backfill_impl_close_failure_does_not_lose_fetched_candles(
    tmp_path, monkeypatch,
):
    """Close-failure path: aclose itself raises AFTER a
    successful fetch → the fetched candles MUST still be written.
    The error counter increments (or does not — depending on how
    the production code classifies an aclose failure); the writer
    lock still runs and commits the row.
    """
    from algotrader_api.ingestion import backfill

    db_file, con = _make_db(tmp_path)
    try:
        _seed_bond(con, figi="BBG000BOND00", bars=0)
        candles = [_make_candle("BBG000BOND00", f"2025-05-{i+1:02d}") for i in range(3)]
        fake = FakeAsyncTinkoffClient(
            candles=candles,
            aclose_fail_with=RuntimeError("close boom"),
        )
        _patch_client_factory(monkeypatch, fake)
        _patch_rate_limit(monkeypatch)

        result = asyncio.run(
            backfill._async_backfill_impl(target_days=30, conn=con),
        )
    finally:
        con.close()

    # Candles must be written even though aclose failed.
    assert result["bars_added"] == 3, (
        f"3 fetched candles must still be written when aclose "
        f"raises; got {result!r}"
    )
    assert fake.aclose_called == 1, (
        f"aclose must be attempted; got aclose_called={fake.aclose_called}"
    )


def test_async_backfill_impl_close_does_not_mask_fetch_error(
    tmp_path, monkeypatch,
):
    """Both-fail path: fetch raises AND aclose raises → the
    fetch error is what propagates (the aclose error is
    suppressed). The error counter still increments.
    """
    from algotrader_api.ingestion import backfill

    db_file, con = _make_db(tmp_path)
    try:
        _seed_bond(con, figi="BBG000BOND00", bars=0)
        fake = FakeAsyncTinkoffClient(
            fetch_fail_with=RuntimeError("fetch boom"),
            aclose_fail_with=RuntimeError("close boom"),
        )
        _patch_client_factory(monkeypatch, fake)
        _patch_rate_limit(monkeypatch)

        result = asyncio.run(
            backfill._async_backfill_impl(target_days=30, conn=con),
        )
    finally:
        con.close()

    assert result["errors"] == 1, (
        f"one fetch error must increment errors exactly once; "
        f"got {result!r}"
    )
    assert fake.aclose_called == 1


def test_async_backfill_impl_closes_client_before_writer_lock_acquisition(
    tmp_path, monkeypatch,
):
    """Order vs writer lock: aclose MUST be called BEFORE
    ``replace_bars_for_figi_with_rowcount`` is called (the
    writer lock is acquired INSIDE that function). This is the
    key invariant: closing the gRPC channel while holding the
    writer lock would block other writers.
    """
    from algotrader_api.ingestion import backfill
    from algotrader_api.db import bars_sqlite

    db_file, con = _make_db(tmp_path)
    try:
        _seed_bond(con, figi="BBG000BOND00", bars=0)
        candles = [_make_candle("BBG000BOND00", f"2025-06-{i+1:02d}") for i in range(3)]
        fake = FakeAsyncTinkoffClient(candles=candles)
        _patch_client_factory(monkeypatch, fake)
        _patch_rate_limit(monkeypatch)

        # The writer lock helper (``writer_lock``) is the
        # signal we hook to detect when the critical section
        # starts. We record the order: aclose_start vs
        # writer_lock acquired.
        order_log: list[str] = []
        original_acquire = fake.aclose

        async def _tracked_acquire():
            order_log.append("aclose_start")
            await original_acquire()
        fake.aclose = _tracked_acquire  # type: ignore[assignment]

        # Patch the writer lock context manager so we can record
        # when the lock is acquired.
        real_writer_lock = bars_sqlite.writer_lock
        from contextlib import contextmanager

        @contextmanager
        def _patched_lock(*args, **kwargs):
            order_log.append("writer_lock_enter")
            try:
                with real_writer_lock(*args, **kwargs):
                    yield
            finally:
                order_log.append("writer_lock_exit")

        bars_sqlite.writer_lock = _patched_lock
        try:
            asyncio.run(
                backfill._async_backfill_impl(target_days=30, conn=con),
            )
        finally:
            bars_sqlite.writer_lock = real_writer_lock
    finally:
        con.close()

    # aclose must run BEFORE the writer lock is acquired. The
    # prefilter + bar-write steps are outside the close
    # caller's ``try/finally``, so the close may complete before
    # or after the writer lock entry — but NEVER block on the
    # lock while the channel is held open. The invariant the
    # production code MUST hold: aclose_start < writer_lock_enter.
    assert order_log, f"order log must not be empty; got {order_log!r}"
    assert "aclose_start" in order_log, f"aclose never recorded; got {order_log!r}"
    assert "writer_lock_enter" in order_log, (
        f"writer lock never entered (zero rows would write); got {order_log!r}"
    )
    assert order_log.index("aclose_start") < order_log.index("writer_lock_enter"), (
        f"aclose must complete BEFORE writer_lock entry; got {order_log!r}"
    )
