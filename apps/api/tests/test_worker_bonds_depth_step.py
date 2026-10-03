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
from unittest.mock import MagicMock, patch

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


def test_step_bonds_depth_does_not_rollback_when_helper_reports_errors(
    fake_db_path: str,
) -> None:
    """Borrower contract: the step borrows the cached connection and
    must NOT call ``rollback`` on it when the helper returns
    ``errors > 0``. Transaction lifecycle (BEGIN/COMMIT/ROLLBACK) is the
    writer helper's responsibility inside its lock; a separate rollback
    from the step would race the helper's own commit/rollback and could
    silently drop a half-written batch — or rollback a batch the helper
    has already committed.

    Same goes for the exception branch: a thrown helper must not
    trigger a reacquire + rollback from the step, which would
    double-rollback a connection the helper may already be holding
    under its own lock.

    Prove both with a ``spec=sqlite3.Connection`` mock so the step's
    real call surface is exercised while still letting us assert
    ``rollback`` was never called and ``get_connection`` was acquired
    exactly once.
    """
    from algotrader_api.db.sqlite import close_all, get_connection

    worker = _import_worker()

    # 1) Helper returns errors>0: step must not rollback.
    errors_result = {
        "figis_processed": 4, "bars_added": 6, "skipped": 12, "errors": 3,
    }
    mock_conn = MagicMock(spec=sqlite3.Connection)
    with patch(
        "algotrader_api.db.sqlite.get_connection", return_value=mock_conn,
    ) as mock_get_conn, patch(
        "algotrader_api.ingestion.backfill.backfill_bonds_to_depth",
        return_value=errors_result,
    ):
        try:
            ok, detail = worker._step_bonds_depth(fake_db_path)
        finally:
            # Clear the cache so subsequent tests get a fresh handle.
            close_all()

    assert ok is False
    assert "errors=3" in detail
    # Borrower contract: exactly one acquire, no rollback.
    assert mock_get_conn.call_count == 1, (
        f"step must acquire cached connection once; got {mock_get_conn.call_count}"
    )
    assert mock_conn.rollback.call_count == 0, (
        "step must NOT call rollback on the borrowed connection when the "
        "helper returns errors; transaction lifecycle belongs to the "
        "writer helper under its own lock"
    )
    # And the step must not have called .close() either.
    assert mock_conn.close.call_count == 0

    # 2) Helper throws: step must not reacquire or rollback either.
    mock_conn2 = MagicMock(spec=sqlite3.Connection)
    boom = RuntimeError("tinkoff channel closed")
    with patch(
        "algotrader_api.db.sqlite.get_connection", return_value=mock_conn2,
    ) as mock_get_conn2, patch(
        "algotrader_api.ingestion.backfill.backfill_bonds_to_depth",
        side_effect=boom,
    ):
        try:
            ok2, detail2 = worker._step_bonds_depth(fake_db_path)
        finally:
            close_all()

    assert ok2 is False
    assert "bonds_depth failed" in detail2
    assert "tinkoff channel closed" in detail2
    # Borrower contract on the exception branch: still one acquire,
    # no rollback, no second reacquire.
    assert mock_get_conn2.call_count == 1, (
        f"step must acquire cached connection once on exception path; "
        f"got {mock_get_conn2.call_count}"
    )
    assert mock_conn2.rollback.call_count == 0, (
        "step must NOT call rollback on the borrowed connection when the "
        "helper throws; same writer-helper-owns-the-lock reason"
    )
    assert mock_conn2.close.call_count == 0
    # And the same real cached-connection handle the production path
    # hands out must still be usable (sanity: the step really is the
    # sole borrower of the module-level cache).
    real_conn = get_connection(fake_db_path)
    try:
        assert real_conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        close_all()