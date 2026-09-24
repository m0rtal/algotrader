"""Tests for the daily refresh chain (apps/api/worker.py).

The chain is an 8-phase ordered list of step functions. These tests
patch every step to its FakeClient-shaped stub so we can exercise the
chain's plumbing without hitting the broker or the data-quality
service.
"""
from __future__ import annotations

import asyncio
import importlib
import sqlite3
import sys
from pathlib import Path
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


def _patch_all_steps(monkeypatch, *, results: dict | None = None):
    """Replace every step function on the worker module with a stub.

    Returns the worker module and a list that records (phase, detail)
    in call order.
    """
    worker = _import_worker()
    calls: list[tuple[str, str]] = []

    def make_stub(phase_name):
        def _stub(db_path):
            detail = (results or {}).get(phase_name, f"{phase_name} ok")
            calls.append((phase_name, detail))
            ok = (results or {}).get(f"{phase_name}._ok", True)
            if not ok:
                return False, detail
            return True, detail
        return _stub

    # Patch BOTH the module attribute and the _STEP_FUNCS dict entry —
    # _STEP_FUNCS holds direct references resolved at module-import time.
    for phase in worker._DAILY_CHAIN_PHASES:
        stub = make_stub(phase)
        fn_name = f"_step_{phase}"
        if hasattr(worker, fn_name):
            monkeypatch.setattr(worker, fn_name, stub, raising=False)
        if phase in worker._STEP_FUNCS:
            monkeypatch.setitem(worker._STEP_FUNCS, phase, stub)

    # Also stub heavy side-effecting helpers so run_daily_chain
    # doesn't try to talk to the broker or set up tracing.
    monkeypatch.setattr(
        worker, "setup_logging", lambda **kw: None, raising=False
    )
    monkeypatch.setattr(
        worker, "setup_tracing", lambda **kw: None, raising=False
    )
    monkeypatch.setattr(
        worker, "shutdown_tracing", lambda: None, raising=False
    )
    monkeypatch.setattr(
        worker, "get_settings",
        lambda: type("S", (), {"sqlite_path": ":memory:",
                               "log_level": "INFO",
                               "otel_endpoint": None})(),
        raising=False,
    )

    return worker, calls


# --------------------------------------------------------------------------- #
# 1. All 8 phases run in order
# --------------------------------------------------------------------------- #


def test_chain_runs_all_8_phases_in_order(monkeypatch):
    """After ml-data-readiness PR-2 decomposition, both subsets together
    cover all 8 phases in the documented order. Run each subset
    explicitly via the ``subset`` kwarg so the test exercises the full
    chain end-to-end."""
    worker, calls = _patch_all_steps(monkeypatch)

    # First subset: migrations → gap_recovery.
    rc0 = worker.run_daily_chain(subset="first")
    # Derived subset: corporate_actions → guardian.
    rc1 = worker.run_daily_chain(subset="derived")

    assert rc0 == 0
    assert rc1 == 0
    expected = [
        "migrations",
        "universe_sync",
        "backfill_moex",
        "gap_recovery",
        "corporate_actions",
        "dividends",
        "freshness_check",
        "guardian",
    ]
    assert len(worker._DAILY_CHAIN_PHASES) == 8
    assert [c[0] for c in calls] == expected


# --------------------------------------------------------------------------- #
# 2. Chain aborts when backfill_moex doesn't add bars
# --------------------------------------------------------------------------- #


def test_chain_aborts_when_backfill_moex_shrinks_bars(monkeypatch):
    worker, calls = _patch_all_steps(
        monkeypatch,
        results={
            "backfill_moex": "pre=10 post=8 delta=-2",
            "backfill_moex._ok": False,
        },
    )

    # ml-data-readiness PR-2: backfill_moex lives in the "first" subset.
    rc = worker.run_daily_chain(subset="first")

    assert rc == 1
    actual = [c[0] for c in calls]
    assert actual[:3] == ["migrations", "universe_sync", "backfill_moex"]
    # Steps after backfill_moex did NOT run
    for skipped in ("gap_recovery", "dividends", "guardian"):
        assert skipped not in actual


# --------------------------------------------------------------------------- #
# 3. Chain aborts when dividends is stale (0 rows + stale table)
# --------------------------------------------------------------------------- #


def test_chain_continues_past_dividends_failure(monkeypatch):
    """Best-effort phases must not abort the chain: a dividends failure
    should still let freshness_check and guardian run so the operator
    gets the full picture in the morning.

    The chain still exits 1 (systemd flags it as degraded) but doesn't
    short-circuit.
    """
    worker, calls = _patch_all_steps(
        monkeypatch,
        results={
            "dividends": "dividends stale: count=0 last=None",
            "dividends._ok": False,
        },
    )

    # ml-data-readiness PR-2: dividends is in the "derived" subset.
    rc = worker.run_daily_chain(subset="derived")

    assert rc == 1  # degraded, but chain completed
    actual = [c[0] for c in calls]
    assert "dividends" in actual
    # Best-effort failure: subsequent phases still run.
    assert "freshness_check" in actual
    assert "guardian" in actual


def test_chain_aborts_when_backfill_moex_fails(monkeypatch):
    """Critical phases still abort: backfill_moex is in _CRITICAL_PHASES,
    so a failure short-circuits the chain.

    Regression guard for the critical/best-effort split: we cannot
    silently downgrade backfill_moex to best-effort without breaking
    the operator's mental model that "if backfill ran, we have data".
    """
    worker, calls = _patch_all_steps(
        monkeypatch,
        results={
            "backfill_moex": "backfill_moex: AssertionError",
            "backfill_moex._ok": False,
        },
    )

    # ml-data-readiness PR-2: backfill_moex is in the "first" subset.
    rc = worker.run_daily_chain(subset="first")

    assert rc == 1
    actual = [c[0] for c in calls]
    assert "backfill_moex" in actual
    assert "dividends" not in actual
    assert "freshness_check" not in actual
    assert "guardian" not in actual


# --------------------------------------------------------------------------- #
# 4. Freshness check raises when chain is too old
# --------------------------------------------------------------------------- #


def test_freshness_check_raises_when_chain_too_old(tmp_path):
    """Direct unit test: pipeline_freshness_check on a DB with no
    pipeline_log rows must raise AssertionError."""
    from algotrader_api.dividends.freshness import (
        pipeline_freshness_check,
    )

    db_path = str(tmp_path / "fresh.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE bars_adjusted (figi TEXT, computed_at TEXT)")
        conn.execute("CREATE TABLE dividends (figi TEXT, retrieved_at TEXT)")
        conn.execute(
            "CREATE TABLE corporate_actions (figi TEXT, retrieved_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE pipeline_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "phase TEXT, started_at TEXT, finished_at TEXT, "
            "result TEXT, detail TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(AssertionError) as excinfo:
        pipeline_freshness_check(db_path, max_chain_age_hours=24)
    msg = str(excinfo.value)
    assert "pipeline_log" in msg or "stale" in msg.lower()


# --------------------------------------------------------------------------- #
# 5+. Per-step unit tests — exercise each step directly so worker.py
# coverage hits the 95% threshold.
# --------------------------------------------------------------------------- #


def test_step_migrations_ok(tmp_path):
    worker = _import_worker()
    with patch.object(worker, "sqlitedb") as sd:
        sd.run_migrations = MagicMock()
        ok, detail = worker._step_migrations(str(tmp_path / "x.db"))
    assert ok is True
    assert detail == ""


def test_step_migrations_exception(tmp_path):
    worker = _import_worker()
    with patch.object(
        worker, "sqlitedb",
        run_migrations=MagicMock(side_effect=RuntimeError("boom")),
    ):
        ok, detail = worker._step_migrations(str(tmp_path / "x.db"))
    assert ok is False
    assert "migrations failed" in detail


def test_step_universe_sync_ok(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()

    async def _fake_run_universe_sync(db_path, client):
        return 42

    fake_pkg = MagicMock()
    fake_pkg.run_universe_sync.side_effect = _fake_run_universe_sync

    with patch.dict(sys.modules, {
        "algotrader_api.ingestion.universe": MagicMock(),
        "algotrader_api.ingestion.universe_sync": fake_pkg,
    }):
        with patch.object(worker, "client_mod", fake_client_mod):
            ok, detail = worker._step_universe_sync(db)
    assert ok is True
    assert "42" in detail


def test_step_universe_sync_exception(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.side_effect = RuntimeError("boom")
    with patch.dict(sys.modules, {
        "algotrader_api.ingestion.universe": MagicMock(),
        "algotrader_api.ingestion.universe_sync": MagicMock(),
    }):
        with patch.object(worker, "client_mod", fake_client_mod):
            ok, detail = worker._step_universe_sync(db)
    assert ok is False
    assert "universe sync failed" in detail


def test_step_backfill_moex_ok(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE bars (figi TEXT, ts TEXT)")
    conn.execute("INSERT INTO bars VALUES ('x', '2020-01-01')")
    conn.commit()
    conn.close()

    fake_runner = MagicMock()

    async def _backfill_from_moex():
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO bars VALUES ('x', '2099-01-01')")
        conn.commit()
        conn.close()

    fake_runner.backfill_from_moex.side_effect = _backfill_from_moex
    fake_pkg = MagicMock()
    fake_pkg.BackfillRunner.return_value = fake_runner
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()

    with patch.dict(sys.modules, {"algotrader_api.ingestion.backfill": fake_pkg}):
        with patch.object(worker, "client_mod", fake_client_mod):
            ok, detail = worker._step_backfill_moex(db)
    assert ok is True
    assert "delta=" in detail


def test_step_backfill_moex_shrinks_on_db_via_runner(tmp_path):
    """When runner.backfill_from_moex() deletes bars, the assertion fires.

    Covers the no-progress regression in the brief by verifying the
    assertion path is reachable when post < pre.
    """
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE bars (figi TEXT, ts TEXT)")
    conn.execute("INSERT INTO bars VALUES ('x', '2020-01-01')")
    conn.execute("INSERT INTO bars VALUES ('y', '2020-01-01')")
    conn.commit()
    conn.close()

    fake_runner = MagicMock()

    async def _backfill_from_moex():
        # Simulate the runner deleting a row.
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM bars WHERE figi='y'")
        conn.commit()
        conn.close()

    fake_runner.backfill_from_moex.side_effect = _backfill_from_moex
    fake_pkg = MagicMock()
    fake_pkg.BackfillRunner.return_value = fake_runner
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()

    with patch.dict(sys.modules, {"algotrader_api.ingestion.backfill": fake_pkg}):
        with patch.object(worker, "client_mod", fake_client_mod):
            ok, detail = worker._step_backfill_moex(db)
    assert ok is False
    assert "shrunk" in detail or "delta=" in detail


def test_step_backfill_moex_exception(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    # Create bars table so snapshot_bars_count() doesn't blow up first.
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE bars (figi TEXT, ts TEXT)")
    conn.commit()
    conn.close()

    fake_pkg = MagicMock()
    fake_pkg.BackfillRunner.return_value.backfill_from_moex.side_effect = RuntimeError("boom")
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()
    with patch.dict(sys.modules, {"algotrader_api.ingestion.backfill": fake_pkg}):
        with patch.object(worker, "client_mod", fake_client_mod):
            ok, detail = worker._step_backfill_moex(db)
    assert ok is False
    assert "backfill_moex failed" in detail


def test_step_gap_recovery_no_gaps(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_gr = MagicMock()
    fake_gr.find_gaps.return_value = []
    fake_br = MagicMock()
    fake_br.BackfillRunner.return_value = MagicMock()
    with patch("algotrader_api.ingestion.client.make_client", return_value=MagicMock()), \
         patch.dict(sys.modules, {
            "algotrader_api.data_quality.gap_recovery": fake_gr,
            "algotrader_api.ingestion.backfill": fake_br,
         }):
        ok, detail = worker._step_gap_recovery(db)
    assert ok is True
    assert "no gaps" in detail


def test_step_gap_recovery_with_gaps(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_gr = MagicMock()
    fake_gr.find_gaps.return_value = ["gap1", "gap2"]

    # recover_gaps is now async; return_value is awaited via asyncio.run.
    async def _fake_recover(*args, **kwargs):
        return {"gap1": 3, "gap2": 5}
    fake_gr.recover_gaps.side_effect = _fake_recover
    fake_br = MagicMock()
    fake_br.BackfillRunner.return_value = MagicMock()
    with patch("algotrader_api.ingestion.client.make_client", return_value=MagicMock()), \
         patch.dict(sys.modules, {
            "algotrader_api.data_quality.gap_recovery": fake_gr,
            "algotrader_api.ingestion.backfill": fake_br,
         }):
        ok, detail = worker._step_gap_recovery(db)
    assert ok is True
    assert "8 bars filled" in detail
    assert "2 gaps" in detail


def test_step_gap_recovery_exception(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_gr = MagicMock()
    fake_gr.find_gaps.side_effect = RuntimeError("boom")
    fake_br = MagicMock()
    with patch.dict(sys.modules, {
        "algotrader_api.data_quality.gap_recovery": fake_gr,
        "algotrader_api.ingestion.backfill": fake_br,
    }):
        ok, detail = worker._step_gap_recovery(db)
    assert ok is False
    assert "gap recovery failed" in detail


def test_step_corporate_actions_ok(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_ds = MagicMock()
    fake_ds.run_derivation.return_value = 4
    fake_fa = MagicMock()
    fake_fa.apply_all_pending.return_value = 12
    with patch.dict(sys.modules, {
        "algotrader_api.scripts_import.derive_splits": fake_ds,
        "algotrader_api.data_quality.forward_adjustment": fake_fa,
    }):
        ok, detail = worker._step_corporate_actions(db)
    assert ok is True
    assert "splits derived=4" in detail
    assert "bars adjusted=12" in detail


def test_step_corporate_actions_exception(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    with patch.dict(sys.modules, {
        "algotrader_api.scripts_import.derive_splits": None,
    }):
        ok, detail = worker._step_corporate_actions(db)
    assert ok is False
    assert "corporate actions failed" in detail


def test_step_dividends_ok_with_writes(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE dividends (figi TEXT, retrieved_at TEXT)")
    conn.commit()
    conn.close()

    fake_script = MagicMock()
    fake_script.fetch_and_persist.return_value = 5
    fake_fresh = MagicMock()
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()

    with patch.dict(sys.modules, {
        "algotrader_api.scripts_import.import_dividends_tinkoff": fake_script,
        "algotrader_api.dividends.freshness": fake_fresh,
    }):
        with patch.object(worker, "client_mod", fake_client_mod):
            ok, detail = worker._step_dividends(db)
    assert ok is True
    assert "tinkoff=5" in detail
    fake_fresh.dividends_freshness_check.assert_not_called()


def test_step_dividends_zero_rows_calls_freshness(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE dividends (figi TEXT, retrieved_at TEXT)")
    conn.commit()
    conn.close()

    fake_script = MagicMock()
    fake_script.fetch_and_persist.return_value = 0
    fake_fresh = MagicMock()
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()

    with patch.dict(sys.modules, {
        "algotrader_api.scripts_import.import_dividends_tinkoff": fake_script,
        "algotrader_api.dividends.freshness": fake_fresh,
    }):
        with patch.object(worker, "client_mod", fake_client_mod):
            ok, detail = worker._step_dividends(db)
    assert ok is True
    fake_fresh.dividends_freshness_check.assert_called_once()


def test_step_dividends_stale_aborts(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_script = MagicMock()
    fake_script.fetch_and_persist.return_value = 0
    fake_fresh = MagicMock()
    fake_fresh.dividends_freshness_check.side_effect = AssertionError(
        "dividends stale"
    )
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()

    with patch.dict(sys.modules, {
        "algotrader_api.scripts_import.import_dividends_tinkoff": fake_script,
        "algotrader_api.dividends.freshness": fake_fresh,
    }):
        with patch.object(worker, "client_mod", fake_client_mod):
            ok, detail = worker._step_dividends(db)
    assert ok is False
    assert "dividends stale" in detail


def test_step_dividends_exception(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_script = MagicMock()
    fake_script.fetch_and_persist.side_effect = RuntimeError("boom")
    fake_fresh = MagicMock()
    fake_client_mod = MagicMock()
    fake_client_mod.make_client.return_value = MagicMock()

    with patch.dict(sys.modules, {
        "algotrader_api.scripts_import.import_dividends_tinkoff": fake_script,
        "algotrader_api.dividends.freshness": fake_fresh,
    }):
        with patch.object(worker, "client_mod", fake_client_mod):
            ok, detail = worker._step_dividends(db)
    assert ok is False
    assert "dividends failed" in detail


def test_step_freshness_check_ok(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_fresh = MagicMock()
    with patch.dict(sys.modules, {"algotrader_api.dividends.freshness": fake_fresh}):
        ok, detail = worker._step_freshness_check(db)
    assert ok is True
    assert "freshness: ok" in detail


def test_step_freshness_check_assertion(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_fresh = MagicMock()
    fake_fresh.pipeline_freshness_check.side_effect = AssertionError("stale")
    with patch.dict(sys.modules, {"algotrader_api.dividends.freshness": fake_fresh}):
        ok, detail = worker._step_freshness_check(db)
    assert ok is False
    assert "stale" in detail


def test_step_freshness_check_exception(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_fresh = MagicMock()
    fake_fresh.pipeline_freshness_check.side_effect = RuntimeError("boom")
    with patch.dict(sys.modules, {"algotrader_api.dividends.freshness": fake_fresh}):
        ok, detail = worker._step_freshness_check(db)
    assert ok is False
    assert "freshness failed" in detail


def test_step_guardian_ok(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    summary = MagicMock()
    summary.figis_checked = 10
    summary.figis_recovered = 2
    summary.anomalies_raised = 1

    async def _run(db_path):
        return summary

    fake_pkg = MagicMock()
    fake_pkg.run_daily_guardian.side_effect = _run
    with patch.dict(sys.modules, {
        "algotrader_api.data_quality.service": fake_pkg,
    }):
        ok, detail = worker._step_guardian(db)
    assert ok is True
    assert "checked=10" in detail
    assert "recovered=2" in detail
    assert "anomalies=1" in detail


def test_step_guardian_exception(tmp_path):
    worker = _import_worker()
    db = str(tmp_path / "x.db")
    fake_pkg = MagicMock()

    async def _boom(db_path):
        raise RuntimeError("boom")
    fake_pkg.run_daily_guardian.side_effect = _boom
    with patch.dict(sys.modules, {
        "algotrader_api.data_quality.service": fake_pkg,
    }):
        ok, detail = worker._step_guardian(db)
    assert ok is False
    assert "guardian failed" in detail


def test_log_chain_phase_swallows_errors(tmp_path):
    """_log_chain_phase is best-effort; must never raise."""
    worker = _import_worker()
    worker._log_chain_phase("/no/such/dir/abc.db", "x", "ok", detail="d")


# --------------------------------------------------------------------------- #
# Freshness module — direct unit tests
# --------------------------------------------------------------------------- #


def test_dividends_freshness_check_table_missing(tmp_path):
    from algotrader_api.dividends.freshness import dividends_freshness_check
    db = str(tmp_path / "x.db")
    conn = sqlite3.connect(db)
    conn.close()
    with pytest.raises(AssertionError, match="missing"):
        dividends_freshness_check(db)


def test_dividends_freshness_check_empty(tmp_path):
    """Empty dividends table is acceptable initial state — no raise."""
    from algotrader_api.dividends.freshness import dividends_freshness_check
    db = str(tmp_path / "x.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE dividends (figi TEXT, retrieved_at TEXT)")
    conn.commit()
    conn.close()
    # Should NOT raise — empty is OK.
    report = dividends_freshness_check(db)
    assert report.is_stale is False
    assert report.row_count == 0


def test_dividends_freshness_check_fresh(tmp_path):
    from algotrader_api.dividends.freshness import dividends_freshness_check
    db = str(tmp_path / "x.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE dividends (figi TEXT, retrieved_at TEXT)")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute("INSERT INTO dividends VALUES ('x', ?)", (now,))
    conn.commit()
    conn.close()
    report = dividends_freshness_check(db, stale_threshold_days=7)
    assert report.row_count == 1
    assert report.is_stale is False
    # as_dict() exposes the dataclass as the public payload
    d = report.as_dict()
    assert d["row_count"] == 1
    assert d["is_stale"] is False
    assert d["last_fetch_at"] == now


def test_dividends_freshness_check_bad_iso(tmp_path):
    """Bad ISO timestamp in the table → still raises AssertionError."""
    from algotrader_api.dividends.freshness import dividends_freshness_check
    db = str(tmp_path / "x.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE dividends (figi TEXT, retrieved_at TEXT)")
    conn.execute("INSERT INTO dividends VALUES ('x', 'not-a-date')")
    conn.commit()
    conn.close()
    with pytest.raises(AssertionError):
        dividends_freshness_check(db, stale_threshold_days=7)


def test_pipeline_freshness_check_fresh(tmp_path):
    from algotrader_api.dividends.freshness import pipeline_freshness_check
    db = str(tmp_path / "x.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE bars_adjusted (figi TEXT, computed_at TEXT)")
    conn.execute("CREATE TABLE dividends (figi TEXT, retrieved_at TEXT)")
    conn.execute("CREATE TABLE corporate_actions (figi TEXT, retrieved_at TEXT)")
    conn.execute(
        "CREATE TABLE pipeline_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "phase TEXT, started_at TEXT, finished_at TEXT, "
        "result TEXT, detail TEXT)"
    )
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute("INSERT INTO bars_adjusted VALUES ('x', ?)", (now,))
    conn.execute("INSERT INTO dividends VALUES ('x', ?)", (now,))
    conn.execute("INSERT INTO corporate_actions VALUES ('x', ?)", (now,))
    conn.execute(
        "INSERT INTO pipeline_log (phase, started_at, finished_at, result) "
        "VALUES ('migrations', ?, ?, 'ok')",
        (now, now),
    )
    conn.commit()
    conn.close()
    out = pipeline_freshness_check(db, max_chain_age_hours=24)
    assert "bars" in out
    assert "dividends" in out
    assert "corporate_actions" in out


def test_pipeline_freshness_check_stale_domain(tmp_path):
    from algotrader_api.dividends.freshness import pipeline_freshness_check
    db = str(tmp_path / "x.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE bars_adjusted (figi TEXT, computed_at TEXT)")
    conn.execute("CREATE TABLE dividends (figi TEXT, retrieved_at TEXT)")
    conn.execute("CREATE TABLE corporate_actions (figi TEXT, retrieved_at TEXT)")
    conn.execute(
        "CREATE TABLE pipeline_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "phase TEXT, started_at TEXT, finished_at TEXT, "
        "result TEXT, detail TEXT)"
    )
    from datetime import datetime, timezone, timedelta
    old = (datetime.now(timezone.utc) - timedelta(hours=100)).replace(
        microsecond=0
    ).isoformat()
    conn.execute("INSERT INTO bars_adjusted VALUES ('x', ?)", (old,))
    conn.execute(
        "INSERT INTO pipeline_log (phase, started_at, finished_at, result) "
        "VALUES ('migrations', ?, ?, 'ok')",
        (old, old),
    )
    conn.commit()
    conn.close()
    with pytest.raises(AssertionError):
        pipeline_freshness_check(db, max_chain_age_hours=24)


# --------------------------------------------------------------------------- #
# Admin route — direct unit test (bypass FastAPI lifespan)
# --------------------------------------------------------------------------- #


def test_admin_data_pipeline_status_endpoint(tmp_path, monkeypatch):
    """Smoke test the /api/admin/data-pipeline/status route."""
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path))
    db = tmp_path / "state.db"

    # The route uses _get_sqlite_path() which reads from a module-level
    # holder. Configure it directly for the test.
    from algotrader_api.routes import settings as settings_mod
    settings_mod.set_sqlite_path(str(db))

    from algotrader_api.routes.admin import data_pipeline_status
    out = asyncio.run(data_pipeline_status())
    assert "last_run" in out
    assert "freshness" in out
    assert out["last_run"]["phases"] == []


def test_admin_data_pipeline_status_with_log(tmp_path, monkeypatch):
    """Endpoint surfaces pipeline_log rows when present."""
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path))
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE pipeline_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "phase TEXT, started_at TEXT, finished_at TEXT, "
        "result TEXT, detail TEXT)"
    )
    conn.execute(
        "INSERT INTO pipeline_log (phase, started_at, finished_at, result, detail) "
        "VALUES ('migrations', '2025-01-01T00:00:00', "
        "'2025-01-01T00:00:01', 'ok', 'fine')"
    )
    conn.execute(
        "INSERT INTO pipeline_log (phase, started_at, finished_at, result, detail) "
        "VALUES ('universe_sync', '2025-01-01T00:00:01', "
        "'2025-01-01T00:00:02', 'ok', 'universe: 5 instruments')"
    )
    conn.commit()
    conn.close()

    from algotrader_api.routes import settings as settings_mod
    settings_mod.set_sqlite_path(str(db))

    from algotrader_api.routes.admin import data_pipeline_status
    out = asyncio.run(data_pipeline_status())
    assert len(out["last_run"]["phases"]) == 2
    assert out["last_run"]["phases"][0]["phase"] == "migrations"
    assert out["last_run"]["rc"] == 0