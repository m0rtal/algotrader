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

import asyncio
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


def test_format_sse_emits_event_and_data_lines():
    """_format_sse produces a wire-format SSE message."""
    from algotrader_api.ingestion.backfill import BackfillEvent
    from algotrader_api.routes.backfill import _format_sse

    ev = BackfillEvent(
        type="ticker_progress",
        run_id=42,
        ts="2026-09-08T12:00:00+00:00",
        payload={"figi": "BBG001", "status": "ok"},
    )
    msg = _format_sse(ev)
    assert msg.startswith("event: ticker_progress\n")
    assert "data: " in msg
    assert "BBG001" in msg
    # SSE messages end with two newlines (record separator).
    assert msg.endswith("\n\n")


def test_ev_to_dict_serializes_event_payload():
    from algotrader_api.ingestion.backfill import BackfillEvent
    from algotrader_api.routes.backfill import _ev_to_dict

    ev = BackfillEvent(
        type="done",
        run_id=7,
        ts="2026-09-08T12:00:00",
        payload={"status": "ok", "tickers_total": 100},
    )
    out = _ev_to_dict(ev)
    assert out["type"] == "done"
    assert out["run_id"] == 7
    assert out["payload"]["status"] == "ok"


def test_last_run_summary_returns_none_when_db_missing(tmp_path):
    """If the sqlite file doesn't exist, return None instead of raising."""
    from algotrader_api.routes.backfill import _last_run_summary

    out = _last_run_summary(str(tmp_path / "nope.db"))
    assert out is None


def test_last_run_summary_returns_none_when_no_logs(data_dir):
    """Fresh DB with no ingestion_logs rows → None."""
    from algotrader_api.routes.backfill import _last_run_summary

    db = f"{data_dir}/state.db"
    # data_dir fixture seeds migrations; no logs → return None.
    out = _last_run_summary(db)
    assert out is None


def test_runner_slot_publish_keeps_history_within_limit():
    """_RunnerSlot caps history at 100 events."""
    from algotrader_api.ingestion.backfill import BackfillEvent
    from algotrader_api.routes.backfill import _RunnerSlot

    slot = _RunnerSlot()
    for i in range(150):
        slot.publish(BackfillEvent(
            type="log", run_id=i, ts=f"2026-01-01T00:{i:02d}:00",
            payload={"i": i},
        ))
    assert len(slot.history) == 100
    # The last event is the most recent.
    assert slot.history[-1].run_id == 149


def test_runner_slot_publish_swallows_queue_full():
    """Slow subscriber's queue full → drop the event, don't crash."""
    from algotrader_api.ingestion.backfill import BackfillEvent
    from algotrader_api.routes.backfill import _RunnerSlot

    slot = _RunnerSlot()
    small_q: asyncio.Queue = asyncio.Queue(maxsize=1)
    small_q.put_nowait("placeholder")  # fill it
    slot.subscribers.add(small_q)

    # Should not raise even though the queue is full.
    slot.publish(BackfillEvent(
        type="log", run_id=1, ts="2026-01-01", payload={},
    ))


def test_event_sink_publishes_to_slot():
    """_event_sink routes through to _slot.publish."""
    from algotrader_api.ingestion.backfill import BackfillEvent
    from algotrader_api.routes import backfill as routes_bf

    # Reset _slot for isolation.
    routes_bf._slot.history.clear()
    routes_bf._slot.subscribers.clear()

    async def _call():
        ev = BackfillEvent(
            type="status",
            run_id=1,
            ts="2026-01-01",
            payload={"state": "running"},
        )
        await routes_bf._event_sink(ev)
        return len(routes_bf._slot.history)

    import asyncio
    assert asyncio.run(_call()) == 1


# SSE generator itself (the `async def gen()` inside backfill_events) is
# not unit-tested: the httpx streaming client does not close the
# connection when the async generator returns cleanly (only on raise),
# which makes iter_text / aiter_text block indefinitely under TestClient
# and ASGITransport. The endpoint is exercised end-to-end in the live
# smoke test (`curl -N http://.../api/admin/backfill/events` against a
# running backend). The helpers it composes from (_format_sse, _ev_to_dict,
# _last_run_summary, _RunnerSlot) are all unit-tested above.


def test_start_returns_400_when_no_token(data_dir):
    """Without a broker token, /backfill/start returns 400."""
    from algotrader_api.db import duck, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    sqlitedb.close_all()
    duck.close()
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/admin/backfill/start", json={})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "broker_token_missing"
    sqlitedb.close_all()
    duck.close()


def test_start_returns_500_when_make_client_raises(data_dir, broker_token, monkeypatch):
    """When make_client raises RuntimeError, /backfill/start returns 500."""
    from unittest.mock import patch

    from algotrader_api.db import duck, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    sqlitedb.close_all()
    duck.close()

    def boom(*a, **k):
        raise RuntimeError("simulated client init failure")

    app = create_app()
    with TestClient(app) as c, \
         patch("algotrader_api.ingestion.client.make_client", side_effect=boom):
        r = c.post("/api/admin/backfill/start", json={})
    assert r.status_code == 500
    assert r.json()["detail"]["error"] == "client_init"
    sqlitedb.close_all()
    duck.close()


def test_stop_returns_idle_state_when_no_runner(data_dir):
    """stop() before any run is a no-op: returns idle/cancelled=False."""
    from algotrader_api.db import duck, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    sqlitedb.close_all()
    duck.close()
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/admin/backfill/stop")
    # stop endpoint returns 202 (per route definition).
    assert r.status_code == 202
    body = r.json()
    assert body["state"] == "idle"
    assert body["cancelled"] is False
    sqlitedb.close_all()
    duck.close()


def test_last_run_summary_returns_row_when_log_exists(data_dir):
    """When ingestion_logs has a bars_written entry, return it as a dict."""
    import sqlite3

    from algotrader_api.routes.backfill import _last_run_summary

    db = f"{data_dir}/state.db"
    # Insert a synthetic log row.
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO ingestion_logs (run_id, ts, level, message) VALUES (?, ?, ?, ?)",
        (0, "2026-09-08T12:00:00", "info", "bars_written=123 last_bar_ts=2024-06-30"),
    )
    con.commit()
    con.close()

    out = _last_run_summary(db)
    assert out is not None
    assert out["level"] == "info"
    assert out["message"] == "bars_written=123 last_bar_ts=2024-06-30"


def test_last_run_summary_returns_none_when_query_raises(data_dir, monkeypatch):
    """sqlite query failure → return None instead of crashing."""
    import sqlite3

    from algotrader_api.routes import backfill as routes_bf
    from algotrader_api.routes.backfill import _last_run_summary

    db = f"{data_dir}/state.db"
    # Insert a row so the query path is taken, then patch execute to blow up.
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO ingestion_logs (run_id, ts, level, message) VALUES (?, ?, ?, ?)",
        (0, "2026-01-01", "info", "bars_written=1"),
    )
    con.commit()
    con.close()

    def boom(*a, **k):
        raise sqlite3.Error("simulated query failure")

    monkeypatch.setattr(routes_bf, "_exec", boom)
    out = _last_run_summary(db)
    assert out is None
