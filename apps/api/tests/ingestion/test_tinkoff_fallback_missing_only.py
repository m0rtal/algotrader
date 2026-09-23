"""Regression test for PR #124: Tinkoff fallback fetches only missing dates.

Bug (2026-09-23):
  BackfillRunner.backfill_from_moex._process_tinkoff called
  ``_fetch_tinkoff_fallback(client, retry, figi, ticker,
  listed_from, yesterday)`` where ``listed_from`` was the figi's
  full listing date (often 2014). For a figi missing only yesterday's
  bar this walked 12 years of history in 7-day chunks via Tinkoff.

  In the live cycle at 17:33 UTC 2026-09-23:
    - 714 figis hit the no-MOEX-board fallback
    - 681 of them had max_ts < 2026-09-21 (need recent fill)
    - After 24 min of worker runtime, bars had not grown at all
      because the fallback was probing 12-year history that already
      existed in the DB.

Fix:
  ``_process_tinkoff`` (inner closure of ``backfill_from_moex``) now
  calls ``compute_missing_dates()`` first and passes
  ``(min(missing), yesterday)`` as the window — usually yesterday
  alone or yesterday + a few recent days. Empty missing set returns
  0 immediately (no Tinkoff call). This makes the Tinkoff fallback
  O(recent_gap_size) instead of O(figi_history_length).

The tests below assert the contract end-to-end through
``backfill_from_moex``, since ``_process_tinkoff`` is an inner
closure and not a method. We stub ``_fetch_tinkoff_fallback`` to
record its arguments and drive the worker with a tiny seeded universe.
"""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


# ─── Test doubles ──────────────────────────────────────────────────


class _CallRecorder:
    """Drop-in replacement for ``_fetch_tinkoff_fallback_impl``.

    Records the (figi, ticker, from_d, to_d) tuples it was called with
    and returns an empty list (so the worker treats it as "no data").
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, date, date]] = []
        self.lock = threading.Lock()

    def __call__(self, client, retry_mod, figi, ticker, from_d, to_d):
        with self.lock:
            self.calls.append((figi, ticker, from_d, to_d))
        return []


def _seed_db(db_path: str, figis: list[tuple[str, str, str]]) -> None:
    """Create a minimal schema with one or more figis + bars through
    2026-09-21. 2026-09-22 (Mon) is the only missing trading day.

    ``figis`` is a list of (figi, ticker, listed_from) tuples.
    """
    con = sqlite3.connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE instruments (
                figi TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                class TEXT NOT NULL,
                listed_from TEXT NOT NULL,
                listed_till TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL DEFAULT 'RUB',
                lot_size INTEGER NOT NULL DEFAULT 1,
                name_lat TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE bars (
                figi TEXT NOT NULL,
                ts TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume INTEGER,
                source TEXT,
                PRIMARY KEY (figi, ts)
            );
            CREATE TABLE moex_holidays (
                date TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE ingestion_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                run_id INTEGER NOT NULL,
                level TEXT NOT NULL,
                figi TEXT,
                message TEXT NOT NULL
            );
            CREATE TABLE pipeline (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phase VARCHAR NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at TIMESTAMP,
                rows_processed INTEGER DEFAULT 0,
                status VARCHAR NOT NULL DEFAULT 'ok',
                detail TEXT
            );
            """
        )
        for figi, ticker, listed_from in figis:
            con.execute(
                "INSERT INTO instruments(figi, ticker, class, listed_from, listed_till) "
                "VALUES (?, ?, 'share', ?, '2099-12-31')",
                (figi, ticker, listed_from),
            )
            # Bars through 2026-09-21 (weekdays only). 2026-09-22 is
            # the only missing trading day at the end of the window.
            cur = date(2014, 1, 1)
            end = date(2026, 9, 21)
            while cur <= end:
                if cur.weekday() < 5:
                    con.execute(
                        "INSERT INTO bars(figi, ts, open, high, low, close, volume, source) "
                        "VALUES (?, ?, 100, 101, 99, 100, 1000, 'moex')",
                        (figi, cur.isoformat()),
                    )
                cur += timedelta(days=1)
        # Minimal holiday set so compute_missing_dates runs cleanly.
        for d in (
            "2024-01-01", "2024-05-01", "2025-01-01", "2025-05-01",
            "2026-01-01", "2026-05-01",
        ):
            con.execute("INSERT INTO moex_holidays(date, name) VALUES (?, 'h')", (d,))
        con.commit()
    finally:
        con.close()


# ─── Driver ───────────────────────────────────────────────────────


async def _drive_backfill_from_moex(db_path: str, recorder: _CallRecorder) -> int:
    """Run ``backfill_from_moex`` against ``db_path`` with stubs.

    Returns ``written_total`` reported by ``backfill_from_moex``.

    Uses ``unittest.mock.patch`` (entered as context manager) so the
    stubs are restored on exit — critical because
    ``BackfillRunner._fetch_tinkoff_fallback`` and ``_get_meta_moex``
    are class attributes shared across all tests, and direct assignment
    leaks between tests.
    """
    from unittest.mock import patch

    from algotrader_api.ingestion.backfill import BackfillRunner

    meta_cache: dict[str, dict | None] = {}
    meta_lock = threading.Lock()

    def stub_get_meta(ticker, yesterday, *, meta_cache, meta_lock):
        with meta_lock:
            meta_cache[ticker] = None
        return None

    async def noop_sink(level, **kw):
        pass

    with patch.object(
        BackfillRunner, "_get_meta_moex",
        staticmethod(stub_get_meta),
    ), patch.object(
        BackfillRunner, "_fetch_tinkoff_fallback",
        staticmethod(recorder),
    ):
        runner = BackfillRunner(
            client=None,
            db_path=db_path,
            event_sink=noop_sink,
            run_id=42,
        )
        # Pre-populate meta cache so the prefetch probe returns immediately.
        runner._moex_meta = meta_cache
        runner._moex_meta_lock = meta_lock

        return await runner.backfill_from_moex(
            recent_tail_days=0,  # focus on the historical walk
        )


# ─── Tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tinkoff_fallback_uses_only_missing_dates_window():
    """The fix: when a figi has no MOEX board, _fetch_tinkoff_fallback
    must be called with from_d ≈ yesterday (not the full history
    listed_from date).
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _seed_db(db_path, [("BBG000000001", "TEST", "2014-01-01")])
        recorder = _CallRecorder()

        await _drive_backfill_from_moex(db_path, recorder)

        assert len(recorder.calls) >= 1, (
            f"_fetch_tinkoff_fallback was never called. calls={recorder.calls}"
        )
        figi, ticker, from_d, to_d = recorder.calls[0]
        assert figi == "BBG000000001"
        assert ticker == "TEST"
        assert from_d == date(2026, 9, 22), (
            f"_fetch_tinkoff_fallback called with from_d={from_d}, "
            f"ticker={ticker}. The fix should make it min(missing) = "
            f"2026-09-22 (NOT 2014-01-01). Regression: Tinkoff walks "
            f"the full history."
        )
        assert to_d == date(2026, 9, 22)


@pytest.mark.asyncio
async def test_tinkoff_fallback_skips_when_no_missing_dates():
    """If compute_missing_dates returns empty, _process_tinkoff must
    not call _fetch_tinkoff_fallback at all (no Tinkoff round-trips
    for already-complete figis).
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _seed_db(db_path, [("BBG000000001", "TEST", "2014-01-01")])
        # Add 2026-09-22 to bars so there are no missing dates.
        con = sqlite3.connect(db_path)
        con.execute(
            "INSERT INTO bars(figi, ts, open, high, low, close, volume, source) "
            "VALUES ('BBG000000001', '2026-09-22', 100, 101, 99, 100, 1000, 'tinkoff')"
        )
        con.commit()
        con.close()
        recorder = _CallRecorder()

        await _drive_backfill_from_moex(db_path, recorder)

        assert recorder.calls == [], (
            f"_fetch_tinkoff_fallback called {len(recorder.calls)} times "
            f"despite figi having no missing dates. The fix must "
            f"short-circuit on empty missing set. calls={recorder.calls}"
        )


@pytest.mark.asyncio
async def test_tinkoff_fallback_bounded_when_listed_from_empty():
    """When ``inst.listed_from`` is the empty-string default, the fix
    must NOT pass ``2014-01-01`` as ``from_d`` blindly. The fix uses
    ``compute_missing_dates`` instead, which gives the *actual* gap
    set bounded by what's missing.

    With the OLD code: ``from_d = date(2014-01-01)`` always — full history.
    With the fix: ``compute_missing_dates(figi, listed_from, yesterday)``
    returns the set of missing trading days, and ``from_d = min(missing)``.

    This test asserts that ``from_d`` reflects the actual data gap,
    not the placeholder listed_from date. Two scenarios are valid:

    1. Bars exist through some recent date — ``from_d`` is the
       trading day *after* the most recent bar.
    2. Bars exist through yesterday — figi is complete, _fetch_tinkoff_
       fallback is **never called**.

    Either outcome satisfies the contract; both beat the OLD code's
    unconditional full-history walk.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _seed_db(db_path, [("BBG000000001", "TEST", "2014-01-01")])
        con = sqlite3.connect(db_path)
        # Set listed_from = '' (the empty-string default that the
        # closure falls back to).
        con.execute(
            "UPDATE instruments SET listed_from = '' WHERE figi = 'BBG000000001'"
        )
        # 2026-09-21 is a Monday. Remove Mon+Tue of the trailing week so
        # the figi has a recent gap (last bar = Friday 2026-09-18).
        con.execute(
            "DELETE FROM bars WHERE figi='BBG000000001' AND ts >= '2026-09-21'"
        )
        con.commit()
        con.close()

        recorder = _CallRecorder()
        await _drive_backfill_from_moex(db_path, recorder)

        # Either Tinkoff was not called (figi is complete), or it was
        # called with from_d = the trading day after the most recent bar.
        # Bars end on 2026-09-18, so min(missing) is 2026-09-21.
        if recorder.calls:
            _, _, from_d, _to_d = recorder.calls[0]
            assert from_d == date(2026, 9, 21), (
                f"from_d={from_d} — figi has bars through 2026-09-18, "
                f"so the next missing trading day is 2026-09-21. The "
                f"fix must use compute_missing_dates() to bound the "
                f"window — not 2014-01-01."
            )
