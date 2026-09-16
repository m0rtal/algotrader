"""Tests for worker.py real _step_universe_sync and _step_backfill_moex."""
import importlib
import sqlite3
import pathlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# worker.py lives in apps/api/, not in src/. Add it to sys.path so we
# can import it the same way the worker runs (`python worker.py`).
_APPS_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_APPS_API))


def _fresh_worker():
    """Re-import worker so we share its module instance with sibling
    tests that pop and reimport the module (otherwise they patch a
    stale module instance)."""
    sys.modules.pop("worker", None)
    return importlib.import_module("worker")


@pytest.fixture
def db_with_bars(tmp_path: pathlib.Path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE bars (figi TEXT, ts TEXT, open REAL, high REAL,"
        "  low REAL, close REAL, volume INTEGER);"
        "INSERT INTO bars VALUES ('F1','2024-01-01',100,100,100,100,1000);"
        "CREATE TABLE pipeline_log ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  phase TEXT, started_at TEXT, finished_at TEXT, result TEXT, detail TEXT);"
        "CREATE TABLE instruments ("
        "  ticker TEXT PRIMARY KEY, figi TEXT NOT NULL UNIQUE,"
        "  class TEXT NOT NULL, name TEXT NOT NULL,"
        "  currency TEXT NOT NULL, lot_size INTEGER NOT NULL,"
        "  isin TEXT, sector TEXT);"
        "CREATE TABLE moex_holidays (date TEXT PRIMARY KEY, name TEXT);"
    )
    conn.commit()
    conn.close()
    return str(db)


def test_step_universe_sync_real_calls_discover(db_with_bars):
    """Real implementation: invokes run_universe_sync wrapper which
    calls universe.discover_universe + universe.upsert_instruments."""
    worker_module = _fresh_worker()
    fake_client = MagicMock()

    # Patch make_client via its original module path; run_universe_sync
    # via its module path. worker_module.client_mod is an alias for
    # algotrader_api.ingestion.client, but patching the alias string
    # doesn't reliably work in newer pytest-mock versions.
    with patch("algotrader_api.ingestion.client.make_client", return_value=fake_client), \
         patch("algotrader_api.ingestion.universe_sync.run_universe_sync",
               new=AsyncMock(return_value=0)) as mock_sync:
        ok, detail = worker_module._step_universe_sync(db_with_bars)
    # Confirm the wrapper was hit (not a count-only no-op):
    assert mock_sync.called, "run_universe_sync must be called in real impl"
    assert ok is True
    assert "0 instruments" in detail


def test_step_backfill_moex_aborts_when_bars_shrink(db_with_bars):
    """If BackfillRunner ends up writing 0 bars (rate-limit, dead
    ticker), the assertion fires and the chain aborts."""
    worker_module = _fresh_worker()
    fake_client = MagicMock()
    fake_runner = MagicMock()

    # `_step_backfill_moex` imports `BackfillRunner` from
    # `algotrader_api.ingestion.backfill` inside the function, so patch
    # the source module rather than `worker_module.BackfillRunner`.
    with patch("algotrader_api.ingestion.client.make_client", return_value=fake_client), \
         patch("algotrader_api.ingestion.backfill.BackfillRunner", return_value=fake_runner):
        # BackfillRunner returns 0 (no bars added):
        fake_runner.backfill_from_moex = AsyncMock()
        # `worker.py` imports `assert_bars_increased` at module level so
        # `worker.assert_bars_increased` is the correct attribute path.
        with patch("worker.assert_bars_increased",
                   side_effect=AssertionError("bars_count shrunk")):
            ok, detail = worker_module._step_backfill_moex(db_with_bars)
    assert ok is False
    assert "shrunk" in detail
