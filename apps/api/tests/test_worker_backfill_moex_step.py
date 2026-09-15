from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# worker.py lives in apps/api/, not in src/. Add it to sys.path so we
# can import it the same way the worker runs (`python worker.py`).
_APPS_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_APPS_API))

import pytest

import worker  # noqa: E402


@pytest.fixture
def fake_db_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    from algotrader_api.db.sqlite import run_migrations
    from algotrader_api.db.migrations import MIGRATIONS_DIR
    run_migrations(db_path, MIGRATIONS_DIR)
    import sqlite3
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) "
        "VALUES ('SBER', 'BBG004730N88', 'share', 'Sber', 'rub', 10)"
    )
    con.commit()
    con.close()
    return db_path


def test_step_backfill_moex_calls_runner(fake_db_path):
    """Worker step calls BackfillRunner.backfill_from_moex."""
    calls = {"n": 0}

    async def _fake_backfill():
        calls["n"] += 1
        return 137

    fake_runner = MagicMock()
    fake_runner.backfill_from_moex = _fake_backfill
    with patch("worker.client_mod.make_client", return_value=MagicMock()):
        with patch("algotrader_api.ingestion.backfill.BackfillRunner", return_value=fake_runner) as MockCls:
            with patch(
                "worker.assert_bars_increased",
                return_value=(0, 137, 137),
            ):
                ok, detail = worker._step_backfill_moex(fake_db_path)
    assert ok is True
    assert "137" in detail or "moex" in detail.lower()
    assert calls["n"] == 1, f"backfill_from_moex called {calls['n']} times, want 1"
    MockCls.assert_called_once()
    assert MockCls.call_args.kwargs["db_path"] == fake_db_path


def test_step_backfill_moex_returns_false_on_exception(fake_db_path):
    fake_runner = MagicMock()
    fake_runner.backfill_from_moex = MagicMock(side_effect=RuntimeError("MOEX ISS down"))
    with patch("worker.client_mod.make_client", return_value=MagicMock()):
        with patch("algotrader_api.ingestion.backfill.BackfillRunner", return_value=fake_runner):
            ok, detail = worker._step_backfill_moex(fake_db_path)
    assert ok is False
    assert "MOEX ISS down" in detail or "failed" in detail.lower()


def test_daily_chain_phases_includes_backfill_moex_not_tinkoff():
    """Phase list replaces daily_backfill+full_history with backfill_moex."""
    assert "backfill_moex" in worker._DAILY_CHAIN_PHASES
    assert "daily_backfill" not in worker._DAILY_CHAIN_PHASES
    assert "full_history" not in worker._DAILY_CHAIN_PHASES
    assert worker._STEP_FUNCS["backfill_moex"] is worker._step_backfill_moex
