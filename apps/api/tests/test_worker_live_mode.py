"""Tests for worker.live mode — autonomous continuous pipeline loop.

These tests cover apps/api/worker.py:run_live_mode() and the new
heartbeat helper _heartbeat_loop(). They use mocked run_daily_chain /
time.sleep / sqlitedb.get_connection so we never touch the real
broker, network, or SQLite file.

TDD discipline: this file was written RED first; implementation landed
in apps/api/worker.py afterwards.

Spec contract: openspec/.../specs/data-quality/spec.md — Requirement:
Autonomous Pipeline Liveness (Scenario: Worker process runs in `live`
mode + Scenario: Worker writes a heartbeat every 30 seconds).
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# Ensure apps/api/src AND apps/api are importable when pytest is invoked
# from the repo root (apps/api itself is the worker.py location).
_API_ROOT = Path(__file__).resolve().parent.parent
_API_SRC = _API_ROOT / "src"
for _p in (str(_API_SRC), str(_API_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _import_worker():
    """Reload the worker module fresh so patched symbols stick."""
    sys.modules.pop("worker", None)
    return importlib.import_module("worker")


def _stub_settings(sqlite_path: str = ":memory:"):
    """Return a settings-shaped object usable by get_settings()."""
    return type(
        "S",
        (),
        {
            "sqlite_path": sqlite_path,
            "log_level": "INFO",
            "otel_endpoint": None,
        },
    )()


# --------------------------------------------------------------------------- #
# run_live_mode: cycle completes then sleeps for the configured interval
# --------------------------------------------------------------------------- #


def test_run_live_mode_runs_cycle_then_sleeps(monkeypatch):
    """run_live_mode should call run_daily_chain, write a pipeline_runs
    row, and sleep for LIVE_INTERVAL_SECONDS (default 1800) before the
    next cycle."""
    worker = _import_worker()

    monkeypatch.setattr(worker, "get_settings", lambda: _stub_settings(), raising=False)
    monkeypatch.setattr(worker, "setup_logging", lambda **kw: None, raising=False)

    # Capture every time.sleep call so we can assert on the interval used
    # after a successful cycle.
    sleeps: list[float] = []
    # Replace worker's reference only: shared time.sleep also serves
    # migration retries and background tasks in other modules.
    monkeypatch.setattr(
        worker, "time", SimpleNamespace(sleep=lambda s: sleeps.append(float(s)))
    )
    monkeypatch.setattr(worker, "_heartbeat_loop", lambda *args: None)

    # Mock the daily chain — return 0 (success) once, then have the loop
    # exit by raising SystemExit on the second call.
    call_count = {"n": 0}

    def fake_run_daily_chain():
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise SystemExit(0)
        return 0

    monkeypatch.setattr(worker, "run_daily_chain", fake_run_daily_chain, raising=False)

    write_calls: list[dict] = []

    def fake_write_pipeline_run(db_path, started_iso, finished_iso, rc, stale_2d):
        write_calls.append(
            {
                "db_path": db_path,
                "started_iso": started_iso,
                "finished_iso": finished_iso,
                "rc": rc,
                "stale_2d": stale_2d,
            }
        )

    monkeypatch.setattr(
        worker, "_write_pipeline_run", fake_write_pipeline_run, raising=False
    )

    # Stale-count query inside run_live_mode uses sqlitedb.get_connection
    # — supply a context-manager-compatible in-memory connection.
    fake_con = sqlite3.connect(":memory:")
    fake_con.execute(
        "CREATE TABLE bars (figi TEXT, ts TEXT)"
    )  # no rows => stale_2d = 0
    monkeypatch.setattr(
        worker.sqlitedb,
        "get_connection",
        MagicMock(return_value=fake_con),
        raising=False,
    )

    # Default LIVE_INTERVAL_SECONDS = 1800
    monkeypatch.delenv("LIVE_INTERVAL_SECONDS", raising=False)

    with pytest.raises(SystemExit):
        worker.run_live_mode()

    assert call_count["n"] >= 1, "run_daily_chain must be invoked"
    assert len(write_calls) >= 1, "_write_pipeline_run must be invoked"
    assert write_calls[0]["rc"] == 0
    assert write_calls[0]["stale_2d"] >= 0
    # The first sleep after a successful cycle uses the configured
    # LIVE_INTERVAL_SECONDS (default 1800).
    assert any(s == 1800.0 for s in sleeps), (
        f"expected a sleep of 1800 (LIVE_INTERVAL_SECONDS), got {sleeps}"
    )


# --------------------------------------------------------------------------- #
# run_live_mode: bounded retry with exponential backoff on failure
# --------------------------------------------------------------------------- #


def test_run_live_mode_retries_on_failure_with_backoff(monkeypatch):
    """First cycle returns rc=1; run_live_mode must sleep using the
    backoff formula (60 * 3**(failures-1)) = 60s for first failure,
    NOT the full LIVE_INTERVAL_SECONDS."""
    worker = _import_worker()

    monkeypatch.setattr(worker, "get_settings", lambda: _stub_settings(), raising=False)
    monkeypatch.setattr(worker, "setup_logging", lambda **kw: None, raising=False)

    sleeps: list[float] = []
    # Replace worker's reference only: shared time.sleep also serves
    # migration retries and background tasks in other modules.
    monkeypatch.setattr(
        worker, "time", SimpleNamespace(sleep=lambda s: sleeps.append(float(s)))
    )
    monkeypatch.setattr(worker, "_heartbeat_loop", lambda *args: None)

    # Cycle 1 -> rc=1 (failure), cycle 2 -> rc=0 (success) then SystemExit
    cycle_results = iter([1, 0])

    def fake_run_daily_chain():
        try:
            return next(cycle_results)
        except StopIteration:
            raise SystemExit(0)

    monkeypatch.setattr(worker, "run_daily_chain", fake_run_daily_chain, raising=False)

    monkeypatch.setattr(
        worker,
        "_write_pipeline_run",
        lambda *a, **kw: None,
        raising=False,
    )

    fake_con = sqlite3.connect(":memory:")
    fake_con.execute("CREATE TABLE bars (figi TEXT, ts TEXT)")
    monkeypatch.setattr(
        worker.sqlitedb,
        "get_connection",
        MagicMock(return_value=fake_con),
        raising=False,
    )

    monkeypatch.delenv("LIVE_INTERVAL_SECONDS", raising=False)

    with pytest.raises(SystemExit):
        worker.run_live_mode()

    # First sleep must be the backoff (60s for first failure),
    # NOT 1800s (the LIVE_INTERVAL_SECONDS interval).
    assert sleeps, "expected time.sleep to be called"
    assert sleeps[0] == 60.0, (
        f"first sleep must be the 60s backoff after rc=1, got {sleeps[0]}"
    )


# --------------------------------------------------------------------------- #
# run_live_mode: writes pipeline_runs row with the expected fields
# --------------------------------------------------------------------------- #


def test_run_live_mode_writes_pipeline_runs_row(monkeypatch):
    """_write_pipeline_run must be called once per cycle with started_iso
    (str), finished_iso (str), rc (int) and a non-negative stale_2d_count."""
    worker = _import_worker()

    monkeypatch.setattr(worker, "get_settings", lambda: _stub_settings(), raising=False)
    monkeypatch.setattr(worker, "setup_logging", lambda **kw: None, raising=False)

    monkeypatch.setattr(worker, "time", SimpleNamespace(sleep=lambda s: None))
    monkeypatch.setattr(worker, "_heartbeat_loop", lambda *args: None)

    captured: dict = {}

    # First call: return 0 (success) so the cycle's bookkeeping
    # (_write_pipeline_run) runs and captures the kwargs. Second call:
    # SystemExit to terminate the live loop.
    def fake_run_daily_chain():
        if "done" not in captured:
            captured["done"] = True
            return 0
        raise SystemExit(0)

    monkeypatch.setattr(worker, "run_daily_chain", fake_run_daily_chain, raising=False)

    def fake_write_pipeline_run(db_path, started_iso, finished_iso, rc, stale_2d):
        captured["db_path"] = db_path
        captured["started_iso"] = started_iso
        captured["finished_iso"] = finished_iso
        captured["rc"] = rc
        captured["stale_2d"] = stale_2d

    monkeypatch.setattr(
        worker, "_write_pipeline_run", fake_write_pipeline_run, raising=False
    )

    fake_con = sqlite3.connect(":memory:")
    fake_con.execute("CREATE TABLE bars (figi TEXT, ts TEXT)")
    fake_con.execute("INSERT INTO bars (figi, ts) VALUES ('BBG000', '2020-01-01')")
    monkeypatch.setattr(
        worker.sqlitedb,
        "get_connection",
        MagicMock(return_value=fake_con),
        raising=False,
    )

    with pytest.raises(SystemExit):
        worker.run_live_mode()

    assert "started_iso" in captured, (
        f"_write_pipeline_run was never called; captured={list(captured)}"
    )
    assert isinstance(captured["started_iso"], str)
    assert isinstance(captured["finished_iso"], str)
    assert captured["rc"] == 0
    assert isinstance(captured["stale_2d"], int)
    assert captured["stale_2d"] >= 0


# --------------------------------------------------------------------------- #
# _heartbeat_loop: writes a row to pipeline_heartbeat within one tick
# --------------------------------------------------------------------------- #


def test_heartbeat_writes_every_30_seconds(tmp_path):
    """_heartbeat_loop must insert/update a pipeline_heartbeat row each
    tick. We override the interval to 0.1s for the test (otherwise we'd
    wait 30s). The helper accepts the interval as a parameter so the
    test doesn't depend on a global."""
    worker = _import_worker()

    db_path = str(tmp_path / "hb.db")
    # Pre-create the table so the heartbeat's INSERT doesn't trip the
    # 'no such table' early-return path.
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE pipeline_heartbeat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_pid INTEGER NOT NULL,
            phase TEXT NOT NULL,
            last_bar_ts TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE bars (figi TEXT, ts TEXT);
        INSERT INTO bars (figi, ts) VALUES ('BBG000', '2024-01-15');
        """
    )
    con.commit()
    con.close()

    stop = threading.Event()
    t = threading.Thread(
        target=worker._heartbeat_loop,
        args=(db_path, stop, 0.1),  # tick every 0.1s for the test
        daemon=True,
    )
    t.start()
    # Let it tick a few times.
    time.sleep(0.35)
    stop.set()
    # Give the thread a moment to exit cleanly after seeing the event.
    t.join(timeout=1.0)

    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT worker_pid, phase, last_bar_ts, updated_at FROM pipeline_heartbeat"
    ).fetchall()
    con.close()

    assert rows, "expected at least one pipeline_heartbeat row"
    # Most recent row should reflect this process, the live_loop phase,
    # and the bar timestamp we inserted above.
    latest = rows[-1]
    assert latest[0] == os.getpid()
    assert latest[1] == "live_loop"
    assert latest[2] == "2024-01-15"
    assert isinstance(latest[3], str) and latest[3]
