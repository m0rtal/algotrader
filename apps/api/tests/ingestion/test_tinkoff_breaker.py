"""Regression tests for PR #129: Tinkoff fallback circuit breaker.

Bug (2026-09-23/24):
  Empty figis (no MOEX board, no bars ever written) trigger Tinkoff
  fallback timeout (45s) on every backfill cycle. With ~700 such
  figis, the backfill_moex phase ran for ~9 hours per cycle waiting
  on Tinkoff to refuse data it never had. Downstream phases
  (corporate_actions, dividends) never ran.

Fix: ``_tinkoff_breaker_is_open`` short-circuits the Tinkoff call
when the breaker is open. ``_tinkoff_breaker_record_failure``
increments a consecutive-failure counter and opens the breaker at
threshold. ``_tinkoff_breaker_record_success`` resets it.

These tests verify the helper functions directly — the integration
test (full ``_process_tinkoff`` with the breaker wired in) is
covered indirectly via existing tests in test_tinkoff_fallback_missing_only.
"""
from __future__ import annotations

import sqlite3
import tempfile
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from algotrader_api.ingestion.backfill import (
    _tinkoff_breaker_is_open,
    _tinkoff_breaker_record_failure,
    _tinkoff_breaker_record_success,
)


def _seed_metadata_db(db_path: str) -> None:
    """Create an empty instrument_metadata schema for tests."""
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE instrument_metadata (
            figi                          TEXT PRIMARY KEY,
            last_bar_ts                   TEXT,
            last_backfilled_at            TEXT,
            total_bars                    INTEGER NOT NULL DEFAULT 0,
            last_run_status               TEXT,
            last_run_at                   TEXT,
            last_error                    TEXT,
            tinkoff_breaker_open          INTEGER NOT NULL DEFAULT 0,
            tinkoff_breaker_open_until    TEXT,
            tinkoff_consecutive_failures  INTEGER NOT NULL DEFAULT 0,
            tinkoff_last_failure_ts       TEXT
        );
        """
    )
    con.commit()
    con.close()


# ─── Tests ────────────────────────────────────────────────────────


def test_breaker_closed_when_no_metadata_row():
    """A figi without an instrument_metadata row at all must not be
    blocked — the breaker only opens after a recorded failure.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _seed_metadata_db(db_path)
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is False


def test_breaker_starts_closed_and_opens_at_threshold():
    """After exactly ``threshold`` consecutive failures the breaker
    opens. Before that, it's still closed.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _seed_metadata_db(db_path)
        # First two failures — still closed.
        _tinkoff_breaker_record_failure(db_path, "BBG000000001", threshold=3)
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is False
        _tinkoff_breaker_record_failure(db_path, "BBG000000001", threshold=3)
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is False
        # Third failure — breaker opens.
        _tinkoff_breaker_record_failure(db_path, "BBG000000001", threshold=3)
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is True


def test_breaker_success_resets_counter_and_closes():
    """A success while the breaker is open must close it and reset
    the counter.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _seed_metadata_db(db_path)
        for _ in range(3):
            _tinkoff_breaker_record_failure(db_path, "BBG000000001", threshold=3)
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is True
        _tinkoff_breaker_record_success(db_path, "BBG000000001")
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is False
        # And the counter is reset — one new failure doesn't reopen.
        _tinkoff_breaker_record_failure(db_path, "BBG000000001", threshold=3)
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is False


def test_breaker_per_figi_isolation():
    """Failures for one figi must not open the breaker for another.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _seed_metadata_db(db_path)
        for _ in range(3):
            _tinkoff_breaker_record_failure(db_path, "BBG000000001", threshold=3)
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is True
        assert _tinkoff_breaker_is_open(db_path, "BBG000000002") is False


def test_breaker_auto_resets_after_window():
    """Manually rewind ``tinkoff_breaker_open_until`` to the past and
    confirm ``_is_open`` returns False — simulating the 24h expiry.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _seed_metadata_db(db_path)
        for _ in range(3):
            _tinkoff_breaker_record_failure(db_path, "BBG000000001", threshold=3)
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is True
        # Simulate "24h passed" by back-dating the open_until.
        con = sqlite3.connect(db_path)
        con.execute(
            "UPDATE instrument_metadata SET tinkoff_breaker_open_until = ? "
            "WHERE figi = 'BBG000000001'",
            ((datetime.now() - timedelta(hours=1)).isoformat(),),
        )
        con.commit()
        con.close()
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is False


def test_breaker_handles_corrupt_open_until_gracefully():
    """If ``tinkoff_breaker_open_until`` is unparseable, treat the
    breaker as open (defensive default) — better to skip than to risk
    a 45s timeout storm.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _seed_metadata_db(db_path)
        con = sqlite3.connect(db_path)
        con.execute(
            "INSERT INTO instrument_metadata(figi, tinkoff_breaker_open, "
            "tinkoff_breaker_open_until) VALUES ('BBG000000001', 1, 'not-a-date')"
        )
        con.commit()
        con.close()
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is True


def test_breaker_writes_open_until_in_future():
    """Sanity: when the breaker opens, ``open_until`` is in the future,
    not the past — the auto-reset actually has something to wait for.
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _seed_metadata_db(db_path)
        before = datetime.now()
        for _ in range(3):
            _tinkoff_breaker_record_failure(db_path, "BBG000000001", threshold=3)
        after = datetime.now()
        con = sqlite3.connect(db_path)
        until_iso = con.execute(
            "SELECT tinkoff_breaker_open_until FROM instrument_metadata "
            "WHERE figi = 'BBG000000001'"
        ).fetchone()[0]
        con.close()
        until_dt = datetime.fromisoformat(until_iso)
        # Open_until should be roughly 24h after now; allow some
        # slack for the record's now() and the comparison now() being
        # captured at slightly different moments.
        expected_min = before + timedelta(hours=23, minutes=55)
        expected_max = after + timedelta(hours=24, minutes=5)
        assert expected_min <= until_dt <= expected_max, (
            f"open_until={until_dt}, expected between "
            f"{expected_min} and {expected_max}"
        )


def test_breaker_concurrent_failures_under_threshold():
    """Three threads recording failure on the same figi must result in
    exactly 3 failures and an open breaker (not lost writes).
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "test.db")
        _seed_metadata_db(db_path)

        def hit():
            _tinkoff_breaker_record_failure(db_path, "BBG000000001", threshold=3)

        threads = [threading.Thread(target=hit) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert _tinkoff_breaker_is_open(db_path, "BBG000000001") is True
