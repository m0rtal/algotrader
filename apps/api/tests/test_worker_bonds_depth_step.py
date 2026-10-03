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


def test_step_bonds_depth_returns_false_when_errors(fake_db_path):
    """When backfill reports ``errors > 0`` the step must return
    ``(False, detail)`` so the daily chain can retry — partial result
    with errors is not an honest success.

    Bounded numeric detail (not the raw exception) keeps the daily-runner
    log lines parsable.
    """
    worker = _import_worker()
    fake_result = {
        "figis_processed": 4, "bars_added": 6, "skipped": 12, "errors": 3,
    }
    with patch(
        "algotrader_api.ingestion.backfill.backfill_bonds_to_depth",
        return_value=fake_result,
    ) as mock_backfill:
        ok, detail = worker._step_bonds_depth(fake_db_path)

    assert ok is False
    # Bounded summary keeps the daily-runner log line stable.
    assert "errors=3" in detail
    # The phase is named so operator grep still works.
    assert "bonds_depth" in detail


def test_step_bonds_depth_does_not_close_cached_connection(fake_db_path):
    """Regression: ``_step_bonds_depth`` previously closed the cached
    sqlite connection that ``db.sqlite.get_connection`` returns. Closing
    the cached connection invalidates it for every subsequent step
    (gap_recovery, corporate_actions, …) and surfaces as
    ``ProgrammingError: Cannot operate on a closed database``.

    The step must NOT close the cached connection. Only the helper that
    owns the connection may close it; here the helper is the shared
    ``get_connection`` cache, so the step must leave the connection
    open and let the test (or operator lifecycle) tear it down via
    ``close_all``.
    """
    worker = _import_worker()
    from algotrader_api.db.sqlite import get_connection

    fake_result = {"figis_processed": 0, "bars_added": 0, "skipped": 0, "errors": 0}
    with patch(
        "algotrader_api.ingestion.backfill.backfill_bonds_to_depth",
        return_value=fake_result,
    ):
        ok, _detail = worker._step_bonds_depth(fake_db_path)

    assert ok is True
    # Same cached connection object must still be usable.
    conn = get_connection(fake_db_path)
    try:
        cur = conn.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
    finally:
        # Test teardown: don't leave a WAL file lying around.
        from algotrader_api.db.sqlite import close_all
        close_all()