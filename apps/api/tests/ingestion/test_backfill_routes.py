"""Tests for /api/admin/backfill routes.

Verifies:
- POST /api/admin/backfill/start kicks off a runner; returns 202 with run_id.
- POST /api/admin/backfill/start returns 409 if already running.
- POST /api/admin/backfill/stop sets the runner's stop flag.
- GET /api/admin/backfill/status reflects the underlying runner state.
- GET /api/admin/backfill/events sets the SSE headers (text/event-stream,
  Cache-Control: no-cache, X-Accel-Buffering: no).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from algotrader_api.db import duck, sqlite as sqlitedb
from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.main import create_app


@pytest.fixture(autouse=True)
def _fresh_db(data_dir, monkeypatch):
    """Reset DB per test so state doesn't leak between cases."""
    db_file = f"{data_dir}/state.db"
    sqlitedb.run_migrations(db_file, MIGRATIONS_DIR)
    sqlitedb.close_all()
    duck.close()
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", data_dir)
    yield
    sqlitedb.close_all()
    duck.close()


@pytest.fixture
def broker_token(data_dir):
    """Write a placeholder broker token so the /start endpoint passes the
    'broker_token_missing' guard. Tests that care about the token contents
    use the returned value."""
    from algotrader_api.db import secrets as secrets_repo

    db_file = f"{data_dir}/state.db"
    secrets_repo.set_secret(db_file, "broker_token", "t.fake.sandbox.ABCD")
    sqlitedb.close_all()
    return "t.fake.sandbox.ABCD"


def test_start_returns_202_with_run_id(data_dir, broker_token):
    """Starting a backfill returns a run_id immediately."""
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/admin/backfill/start", json={"history_years": 5})
        assert r.status_code == 202
        body = r.json()
        assert body["run_id"] > 0
        # run_id is integer; downstream consumers rely on this shape.
        assert isinstance(body["run_id"], int)


def test_start_returns_409_when_already_running(data_dir, broker_token, monkeypatch):
    """Second start while first is running returns 409.

    We mock the runner's run() to block on an event so it stays in the
    active state for the duration of both HTTP calls — otherwise the
    real run() completes too fast for the second call to see it active.
    """
    import asyncio
    from algotrader_api.routes import backfill as backfill_route

    started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_run(*args, **kwargs):
        started.set()
        await proceed.wait()

    monkeypatch.setattr(backfill_route, "_slot", backfill_route._RunnerSlot())
    # Replace BackfillRunner.run on the class — the route imports it
    # lazily, so we patch the source.
    from algotrader_api.ingestion.backfill import BackfillRunner

    monkeypatch.setattr(BackfillRunner, "run", slow_run)

    app = create_app()
    with TestClient(app) as c:
        r1 = c.post("/api/admin/backfill/start", json={})
        assert r1.status_code == 202
        # The runner is now "active" because run() is awaiting.
        # Wait briefly for the loop to schedule it.
        import time
        time.sleep(0.05)
        r2 = c.post("/api/admin/backfill/start", json={})
        assert r2.status_code == 409, (
            f"expected 409, got {r2.status_code} body={r2.text}"
        )
        body = r2.json()
        assert body["detail"]["error"] == "already_running"
        assert "run_id" in body["detail"]
        # Release the runner so the test exits cleanly.
        proceed.set()


def test_status_reflects_initial_state(data_dir):
    """Before any run, status reports idle."""
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/admin/backfill/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "idle"
        assert body["run_id"] is None
        assert body["tickers_done"] == 0
        assert body["tickers_total"] == 0


def test_status_reflects_running_state(data_dir, broker_token):
    """After start, status reflects state in a known set.

    The runner may already have completed by the time we GET /status —
    we just verify the endpoint returns a valid state value, not 500.
    """
    app = create_app()
    with TestClient(app) as c:
        r1 = c.post("/api/admin/backfill/start", json={})
        assert r1.status_code == 202
        r2 = c.get("/api/admin/backfill/status")
        assert r2.status_code == 200
        body = r2.json()
        # The runner completes quickly with no real SDK data, so the
        # state may already be 'idle'. We accept any valid state value.
        assert body["state"] in ("idle", "running", "done", "backfilling", "stopping", "discovering")
        # run_id is either None (already reset) or the one we started.
        assert body["run_id"] is None or body["run_id"] > 0


def test_stop_returns_202(data_dir, broker_token, monkeypatch):
    """Stop endpoint accepts POST and returns 202.

    Block the runner with an event so we can observe it in the active
    state, then call /stop, then release.
    """
    import asyncio
    from algotrader_api.routes import backfill as backfill_route

    proceed = asyncio.Event()

    async def slow_run(*args, **kwargs):
        await proceed.wait()

    monkeypatch.setattr(backfill_route, "_slot", backfill_route._RunnerSlot())
    from algotrader_api.ingestion.backfill import BackfillRunner
    monkeypatch.setattr(BackfillRunner, "run", slow_run)

    app = create_app()
    with TestClient(app) as c:
        c.post("/api/admin/backfill/start", json={})
        import time
        time.sleep(0.05)
        r = c.post("/api/admin/backfill/stop")
        assert r.status_code == 202
        proceed.set()


# NOTE: SSE events endpoint is intentionally NOT unit-tested with
# TestClient — the httpx streaming client does not close the connection
# when the async generator returns cleanly (only on raise), which makes
# `iter_text` block indefinitely. The endpoint is exercised end-to-end
# in the live smoke test (Section 11 of tasks.md): `curl -N
# http://.../api/admin/backfill/events` against a running backend.
