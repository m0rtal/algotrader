"""Tests for worker.py daily mode — the full refresh chain."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# worker.py lives in apps/api/, not in src/. Add it to sys.path so we
# can import it the same way the worker runs (`python worker.py`).
_APPS_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_APPS_API))

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import worker as worker_module  # noqa: E402

run_daily_chain = worker_module.run_daily_chain
_STEP_FUNCS = worker_module._STEP_FUNCS
_DAILY_CHAIN_PHASES = worker_module._DAILY_CHAIN_PHASES


# --------------------------------------------------------------------------- #
# Schema helpers
# --------------------------------------------------------------------------- #


def _init_pipeline_log(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pipeline_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phase TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            result TEXT NOT NULL,
            detail TEXT
        );
        """
    )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_run_daily_chain_executes_all_phases_in_order(tmp_path: Path):
    """After ml-data-readiness PR-2 decomposition, the ``first`` subset
    covers the legacy 4-phase half (migrations → gap_recovery). Run both
    subsets via the kwarg to verify all phases in order end-to-end."""
    _init_pipeline_log(tmp_path / "test.db")

    calls: list[str] = []

    def make_step(name: str):
        def step(db_path: str):
            calls.append(name)
            return True, f"{name} ok"
        return step

    fake_steps = {phase: make_step(phase) for phase in _DAILY_CHAIN_PHASES}

    with patch.dict(_STEP_FUNCS, fake_steps, clear=True):
        # PR #128 (2026-09-24): setup_tracing/shutdown_tracing removed
        # from worker.py; their patches here dropped alongside.
        with patch("worker.get_settings") as gs, \
             patch("worker.setup_logging"):
            gs.return_value = MagicMock(sqlite_path=str(tmp_path / "test.db"))
            run_daily_chain(subset="first")
            run_daily_chain(subset="derived")

    assert calls == list(_DAILY_CHAIN_PHASES)


def test_run_daily_chain_aborts_on_phase_failure(tmp_path: Path):
    """A failing phase stops the chain; later phases don't run."""
    _init_pipeline_log(tmp_path / "test.db")

    calls: list[str] = []

    def make_step(name: str, fail: bool = False):
        def step(db_path: str):
            calls.append(name)
            if fail:
                return False, f"{name} FAILED"
            return True, f"{name} ok"
        return step

    fake_steps = {
        "migrations": make_step("migrations"),
        "universe_sync": make_step("universe_sync"),
        "backfill_moex": make_step("backfill_moex", fail=True),
        "corporate_actions": make_step("corporate_actions"),
        "dividends": make_step("dividends"),
        "guardian": make_step("guardian"),
    }

    with patch.dict(_STEP_FUNCS, fake_steps, clear=True):
        with patch("worker.get_settings") as gs, \
             patch("worker.setup_logging"):
            gs.return_value = MagicMock(sqlite_path=str(tmp_path / "test.db"))
            # ml-data-readiness PR-2: backfill_moex is in the "first" subset.
            rc = run_daily_chain(subset="first")

    assert rc == 1
    assert "backfill_moex" in calls
    assert "corporate_actions" not in calls
    assert "dividends" not in calls
    assert "guardian" not in calls


def test_run_daily_chain_logs_each_step_to_pipeline_log(tmp_path: Path):
    """Every successful phase writes a row with result='ok'.

    ml-data-readiness PR-2: runs both subsets so the union covers all
    8 phases."""
    db_path = tmp_path / "test.db"
    _init_pipeline_log(db_path)

    # Re-import worker so we get the same module instance the chain test
    # suite uses (otherwise its `get_settings` patch misses us).
    sys.modules.pop("worker", None)
    importlib.import_module("worker")
    worker_mod = sys.modules["worker"]
    run_daily_chain = worker_mod.run_daily_chain
    step_funcs = worker_mod._STEP_FUNCS
    phases = worker_mod._DAILY_CHAIN_PHASES

    def ok_step(db_path: str):
        return True, "test"

    fake_steps = {phase: ok_step for phase in phases}

    with patch.dict(step_funcs, fake_steps, clear=True):
        with patch("worker.get_settings") as gs, \
             patch("worker.setup_logging"):
            gs.return_value = MagicMock(sqlite_path=str(db_path))
            run_daily_chain(subset="first")
            run_daily_chain(subset="derived")

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT phase, result FROM pipeline_log ORDER BY id"
    ).fetchall()
    conn.close()
    assert len(rows) == 8  # 8-phase chain (migrations, universe_sync, backfill_moex, gap_recovery, corporate_actions, dividends, freshness_check, guardian)
    assert all(r[1] == "ok" for r in rows)
    assert [r[0] for r in rows] == list(phases)


def test_daily_is_default_mode():
    """`python worker.py` (no args) defaults to daily mode."""
    import inspect
    src = inspect.getsource(worker_module.main)
    assert 'default="daily"' in src
