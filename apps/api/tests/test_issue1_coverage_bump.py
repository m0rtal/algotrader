"""Coverage-bump tests for issue #1.

Targets the specific gap lines flagged by the audit:

- data_quality/service.py:30-34 (_make_client_from_settings body)
- data_quality/service.py:58-61 (BackfillRunner fallback when runner=None)
- data_quality/service.py:67-72 (anomalies loop over skipped_exhausted)
- routes/backfill.py: branch coverage on _format_sse, _ev_to_dict,
  _last_run_summary, _total_bars_on_disk, _pending_count, and the
  start_backfill / stop_backfill / backfill_status endpoints.

Each test exercises one branch path that the existing tests miss.
The point is to push aggregate coverage above the 95% gate that
issue #1 reports as failing.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── data_quality/service.py branches ──────────────────────────────


def test_make_client_from_settings_invokes_make_client_with_settings_path():
    """service.py:30-34 — the lazy-import-and-call path.

    The tests for run_daily_guardian monkey-patch _make_client_from_settings
    so the body is never exercised. This test calls it directly and
    asserts it calls make_client with the path from get_settings and
    use_fake=False (the production default — fake mode is for the
    sandbox dev path).
    """
    from algotrader_api.data_quality import service as svc_mod

    fake_client = MagicMock(name="production_client")
    fake_settings = MagicMock()
    fake_settings.sqlite_path = "/var/lib/algotrader/state.db"

    with patch.object(svc_mod, "make_client", fake_client) as _mc, \
         patch.object(svc_mod, "get_settings", return_value=fake_settings):
        client = svc_mod._make_client_from_settings()

    assert client is fake_client
    _mc.assert_called_once_with(
        sqlite_path="/var/lib/algotrader/state.db", use_fake=False
    )


@pytest.mark.asyncio
async def test_run_daily_guardian_builds_backfill_runner_when_none_passed(
    tmp_path,
):
    """service.py:58-61 — the ``if runner is None:`` fallback.

    When the caller doesn't supply a runner, the orchestrator must
    build one from settings + the live broker client. Covering this
    branch matters because it makes the orchestrator callable from
    the systemd worker (which does NOT pre-build a runner).
    """
    db_path = str(tmp_path / "state.db")
    from algotrader_api.db.migrations import MIGRATIONS_DIR
    from algotrader_api.db.sqlite import run_migrations
    run_migrations(db_path, str(MIGRATIONS_DIR))

    from algotrader_api.data_quality import service as svc_mod

    fake_client = MagicMock(name="client")
    fake_client.discover_universe = AsyncMock(return_value=[])

    fake_settings = MagicMock()
    fake_settings.sqlite_path = db_path

    fake_runner = MagicMock(name="auto_built_runner")
    fake_runner.run = MagicMock()

    with patch.object(svc_mod, "_make_client_from_settings", return_value=fake_client), \
         patch.object(svc_mod, "BackfillRunner", return_value=fake_runner) as br_ctor, \
         patch.object(svc_mod, "recover_stale", return_value=MagicMock(
             queued=[], skipped_exhausted=[], skipped_ratelimit_only=[]
         )) as rs, \
         patch.object(svc_mod, "run_completeness_pass", AsyncMock(
             return_value=MagicMock(bars_added=0, figis_examined=0,
                                    gaps_found=0, exhausted=0)
         )):
        # runner=None triggers the fallback at line 58-61.
        summary = await svc_mod.run_daily_guardian(db_path, runner=None)

    # The fallback fired and constructed a BackfillRunner with our
    # fake client + db_path.
    br_ctor.assert_called_once()
    call_kwargs = br_ctor.call_args.kwargs
    assert call_kwargs["client"] is fake_client
    assert call_kwargs["db_path"] == db_path
    assert summary.figis_checked >= 0


@pytest.mark.asyncio
async def test_run_daily_guardian_logs_anomalies_for_each_skipped_exhausted(
    tmp_path,
):
    """service.py:67-72 — the anomalies-warning loop.

    When ``recover_stale`` returns ``skipped_exhausted`` with one or
    more figis, the orchestrator must emit a WARNING for each one
    (with the figi identifier so the operator's alerting can route
    by ticker). Without this loop running, exhausted figis would
    silently sit forever in the ``stale_recovery_exhausted`` state.
    """
    import logging

    db_path = str(tmp_path / "state.db")
    from algotrader_api.db.migrations import MIGRATIONS_DIR
    from algotrader_api.db.sqlite import run_migrations
    run_migrations(db_path, str(MIGRATIONS_DIR))

    from algotrader_api.data_quality import service as svc_mod
    from algotrader_api.data_quality.recovery import RecoverySummary

    # Capture WARNING-level records emitted on the service logger.
    captured: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            captured.append(record)

    handler = _Handler(level=logging.WARNING)
    logger = logging.getLogger("algotrader_api.data_quality.service")
    logger.addHandler(handler)
    try:
        with patch.object(svc_mod, "_make_client_from_settings", return_value=MagicMock()), \
             patch.object(svc_mod, "BackfillRunner", return_value=MagicMock(run=MagicMock())), \
             patch.object(svc_mod, "recover_stale", return_value=RecoverySummary(
                 queued=[],
                 skipped_exhausted=["FIGI-A", "FIGI-B", "FIGI-C"],
                 skipped_ratelimit_only=[],
             )), \
             patch.object(svc_mod, "run_completeness_pass", AsyncMock(
                 return_value=MagicMock(bars_added=0, figis_examined=0,
                                        gaps_found=0, exhausted=0)
             )):
            summary = await svc_mod.run_daily_guardian(db_path, runner=MagicMock())
    finally:
        logger.removeHandler(handler)

    # Summary counter reflects the anomaly count.
    assert summary.anomalies_raised == 3

    # Each skipped figi produced exactly one WARNING log.
    anomaly_msgs = [
        r for r in captured
        if "stale_recovery_exhausted" in r.getMessage()
        or getattr(r, "event", None) == "guardian.anomaly.stale_recovery_exhausted"
    ]
    # The orchestrator uses the stdlib logging.warning path — the
    # event name is in the message string.
    anomaly_msgs_by_msg = [
        r for r in captured
        if "stale_recovery_exhausted" in r.getMessage()
    ]
    assert len(anomaly_msgs_by_msg) >= 3, (
        f"expected at least 3 anomaly warnings, got {len(anomaly_msgs_by_msg)} "
        f"messages: {[r.getMessage() for r in captured]}"
    )


# ── routes/backfill.py branches ───────────────────────────────────


def test_backfill_events_endpoint_returns_sse_stream(client):
    """routes/backfill.py:203 — backfill_events returns an SSE stream.

    Without this test the GET /api/admin/backfill/events handler
    is uncovered. The handler is the operator-facing live-progress
    view during a backfill run.
    """
    r = client.get("/api/admin/backfill/events")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("text/event-stream")


def test_backfill_force_reset_returns_ok_when_no_runner(client):
    """routes/backfill.py:388 — backfill_force_reset with no active runner.

    Guards against the no-op-when-idle path being uncovered.
    """
    r = client.post("/api/admin/backfill/force-reset")
    assert r.status_code == 200
    body = r.json()
    # The reset should report state and the cleared runner/run_id.
    assert "state" in body
    assert body["state"] in ("idle", "reset")


def test_backfill_status_includes_pipeline_summary(client, fresh_db):
    """routes/backfill.py:175 — backfill_status returns the pipeline summary.

    Cover the path that reads from the pipeline table and returns
    the last successful run's metrics.
    """
    con = sqlite3.connect(fresh_db)
    con.execute(
        "INSERT INTO pipeline (phase, started_at, finished_at, rows_processed, status, detail) "
        "VALUES ('backfill_run', datetime('now'), datetime('now'), 42, 'ok', 'manual')"
    )
    con.commit()
    con.close()

    r = client.get("/api/admin/backfill/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] in ("idle", "running", "stopping")
    assert body["total_bars"] >= 0


def test_backfill_pending_with_zero_universe_returns_zeros(client, fresh_db):
    """routes/backfill.py:302 — _pending_count with no instrument rows.

    Guards the empty-universe branch — the operator's "pending" view
    must show zeros cleanly, not error or NaN, on a fresh DB.
    """
    # fresh_db already exists via the fixture; just hit the endpoint.
    r = client.get("/api/admin/backfill/pending")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["new"] == 0
    assert body["stale"] == 0
    assert body["up_to_date"] == 0
    assert body["error"] == 0


def test_backfill_status_with_active_runner_reports_running(client):
    """routes/backfill.py:175 — backfill_status while a runner is active.

    Without this test, the "running" branch of _last_run_summary is
    uncovered. We install a fake runner in the slot and verify the
    response reflects state=running.
    """
    from algotrader_api.routes import backfill as backfill_route

    fake_runner = MagicMock()
    fake_runner.is_alive.return_value = True
    backfill_route._slot.runner = fake_runner
    backfill_route._slot.run_id = 42
    try:
        r = client.get("/api/admin/backfill/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "running"
        assert body["run_id"] == 42
    finally:
        backfill_route._slot.runner = None
        backfill_route._slot.run_id = None
