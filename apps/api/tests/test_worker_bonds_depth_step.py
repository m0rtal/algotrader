"""Tests for worker._step_bonds_depth.

PR-2 in coverage-and-quality: verifies the daily-chain step
backfills bond figis via backfill_bonds_to_depth with target_days=30
and reports a per-step summary. Patches `worker.backfill_bonds_to_depth`
at the import site so the call surface and result-dict keys match what
the real implementation returns.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# Ensure apps/api/src AND apps/api are importable so `import worker`
# resolves to apps/api/worker.py when pytest runs from any cwd.
_API_ROOT = Path(__file__).resolve().parent.parent
_API_SRC = _API_ROOT / "src"
for _p in (str(_API_SRC), str(_API_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _import_worker():
    """Reload the worker module fresh so patched symbols stick."""
    sys.modules.pop("worker", None)
    return importlib.import_module("worker")


@pytest.fixture
def fake_db_path(tmp_path: Path) -> str:
    """In-memory SQLite with instruments + bars tables; minimum viable for the step."""
    db = tmp_path / "test.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE instruments (
            figi TEXT PRIMARY KEY, ticker TEXT, class TEXT
        );
        CREATE TABLE bars (
            figi TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL,
            volume INTEGER, source TEXT DEFAULT 'moex',
            PRIMARY KEY (figi, ts)
        );
    """)
    con.close()
    return str(db)


def test_step_bonds_depth_calls_backfill_bonds_to_depth(fake_db_path):
    """_step_bonds_depth must call backfill_bonds_to_depth with target_days=30."""
    worker = _import_worker()
    # ``_step_bonds_depth`` does ``from algotrader_api.ingestion.backfill
    # import backfill_bonds_to_depth`` at call time, so patch the source
    # module attribute (not ``worker.backfill_bonds_to_depth``, which is
    # never bound on the worker module).
    fake_result = {"figis_processed": 5, "bars_added": 12, "skipped": 1620, "errors": 0}
    with patch(
        "algotrader_api.ingestion.backfill.backfill_bonds_to_depth",
        return_value=fake_result,
    ) as mock_backfill:
        ok, detail = worker._step_bonds_depth(fake_db_path)

    assert ok is True
    mock_backfill.assert_called_once()
    # Inspect call kwargs to confirm target_days=30
    call_kwargs = mock_backfill.call_args.kwargs
    assert call_kwargs.get("target_days") == 30


def test_step_bonds_depth_logs_skipped_when_no_work(fake_db_path):
    """When backfill returns all-zero result, step still succeeds."""
    worker = _import_worker()
    fake_result = {"figis_processed": 0, "bars_added": 0, "skipped": 0, "errors": 0}
    with patch(
        "algotrader_api.ingestion.backfill.backfill_bonds_to_depth",
        return_value=fake_result,
    ) as mock_backfill:
        ok, detail = worker._step_bonds_depth(fake_db_path)

    assert ok is True
    # detail string contains the per-step summary (follow the pattern of
    # _step_backfill_moex, _step_corporate_actions, etc.)
    assert "bonds_depth" in detail.lower() or "bond" in detail.lower()