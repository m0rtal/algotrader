"""Smoke tests for /api/admin/backfill/* routes after `remove-duckdb-and-parquet`.

The router still exists and is wired in `main.py`, so we re-add a
thin smoke surface to keep coverage healthy.
"""
from __future__ import annotations

import sqlite3


def test_backfill_status_returns_idle_when_no_runner(client):
    r = client.get("/api/admin/backfill/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "idle"
    assert body["total_bars"] >= 0


def test_backfill_pending_returns_breakdown(client, fresh_db):
    con = sqlite3.connect(fresh_db)
    con.executescript(
        """
        INSERT OR IGNORE INTO instruments (ticker, figi, class, name, currency, lot_size)
        VALUES ('TEST-PENDING-SHARE', 'FIGI-TEST-PENDING-S', 'share', 'Test S', 'rub', 10),
               ('TEST-PENDING-BOND', 'FIGI-TEST-PENDING-B', 'bond', 'Test B', 'rub', 1);
        INSERT INTO instrument_metadata
          (figi, last_bar_ts, last_run_status, total_bars)
        VALUES
          ('FIGI-TEST-PENDING-S', '2026-01-01', 'ok', 200),
          ('FIGI-TEST-PENDING-B', NULL, 'error', 0);
        """
    )
    con.commit()
    con.close()

    r = client.get("/api/admin/backfill/pending")
    assert r.status_code == 200
    body = r.json()
    assert "new" in body and "stale" in body and "up_to_date" in body
    assert "error" in body and "total" in body
    assert body["total"] >= 2


def test_backfill_stop_returns_idle_when_no_runner(client):
    """`/backfill/stop` with no active run returns idle (no-op)."""
    r = client.post("/api/admin/backfill/stop")
    assert r.status_code == 202
    body = r.json()
    assert body["state"] == "idle"
    assert body["cancelled"] is False


def test_backfill_stop_returns_stopping_when_runner_active(client):
    """`/backfill/stop` returns stopping + cancelled when a runner is active."""
    from algotrader_api.routes import backfill as backfill_route

    fake_runner = type("FakeRunner", (), {"stop": lambda self: None})()
    backfill_route._slot.runner = fake_runner
    backfill_route._slot.run_id = 99
    try:
        r = client.post("/api/admin/backfill/stop")
        assert r.status_code == 202
        body = r.json()
        assert body["state"] == "stopping"
        assert body["cancelled"] is True
        assert body["run_id"] == 99
    finally:
        backfill_route._slot.reset()


def test_backfill_runner_slot_publish_and_history():
    """Unit test for the RunnerSlot publish logic.

    Verifies that `publish` enqueues an event for every subscriber
    and that the history list is bounded by `_history_limit`.
    """
    import asyncio

    from algotrader_api.routes import backfill as backfill_route

    slot = backfill_route._RunnerSlot()
    slot.subscribers = set()
    q1: asyncio.Queue = asyncio.Queue(maxsize=10)
    q2: asyncio.Queue = asyncio.Queue(maxsize=10)
    slot.subscribers.add(q1)
    slot.subscribers.add(q2)

    ev = backfill_route.BackfillEvent(
        type="status", run_id=1, ts="2026-09-12T14:00:00Z", payload={"x": 1}
    )
    slot.publish(ev)
    assert q1.get_nowait() == ev
    assert q2.get_nowait() == ev

    # History trim: push > _history_limit events and confirm trim.
    for i in range(slot._history_limit + 50):
        slot.publish(
            backfill_route.BackfillEvent(
                type="status",
                run_id=1,
                ts="2026-09-12T14:00:00Z",
                payload={"i": i},
            )
        )
    assert len(slot.history) == slot._history_limit


def test_backfill_sse_formatters():
    """SSE formatters produce the expected event/data SSE wire format."""
    from algotrader_api.routes.backfill import (
        BackfillEvent,
        _ev_to_dict,
        _format_sse,
    )

    ev = BackfillEvent(
        type="ticker_progress",
        run_id=42,
        ts="2026-09-12T14:00:00Z",
        payload={"figi": "FIGI-1"},
    )
    sse = _format_sse(ev)
    assert sse.startswith("event: ticker_progress\ndata: ")
    assert sse.endswith("\n\n")

    d = _ev_to_dict(ev)
    assert d["type"] == "ticker_progress"
    assert d["run_id"] == 42
    assert d["ts"] == "2026-09-12T14:00:00Z"
    assert d["payload"] == {"figi": "FIGI-1"}


def test_backfill_force_reset_wipes_metadata(client, fresh_db):
    con = sqlite3.connect(fresh_db)
    con.executescript(
        """
        INSERT OR IGNORE INTO instruments (ticker, figi, class, name, currency, lot_size)
        VALUES ('TEST-RESET', 'FIGI-TEST-RESET', 'share', 'T', 'rub', 1);
        INSERT INTO instrument_metadata (figi, last_run_status, total_bars)
        VALUES ('FIGI-TEST-RESET', 'ok', 100);
        """
    )
    con.commit()
    con.close()

    r = client.post("/api/admin/backfill/force-reset")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted_rows"] >= 1
    assert "full backfill" in body["next_run"]

    # Verify rows are gone.
    con = sqlite3.connect(fresh_db)
    assert con.execute(
        "SELECT COUNT(*) FROM instrument_metadata WHERE figi = 'FIGI-TEST-RESET'"
    ).fetchone()[0] == 0
    con.close()


def test_backfill_start_returns_400_without_token(client):
    """Without a broker token, start returns 400 broker_token_missing."""
    # No token is set in the test fresh_db, so this branch fires.
    r = client.post("/api/admin/backfill/start", json={})
    assert r.status_code == 400
    # The detail can be either a dict (HTTPException with structured
    # detail) or a string. Just assert the marker is present.
    body = r.text
    assert "broker_token_missing" in body


def test_backfill_start_returns_409_when_already_running(client):
    """When _slot.runner is set, /backfill/start returns 409 already_running."""
    from algotrader_api.routes import backfill as backfill_route

    fake_runner = type("FakeRunner", (), {"stop": lambda self: None})()
    backfill_route._slot.runner = fake_runner
    backfill_route._slot.run_id = 7
    try:
        r = client.post("/api/admin/backfill/start", json={})
        assert r.status_code == 409
        body = r.json()
        # FastAPI wraps HTTPException(detail=dict) as {"detail": ...}
        detail = body.get("detail", body)
        assert detail["error"] == "already_running"
        assert detail["run_id"] == 7
    finally:
        backfill_route._slot.reset()


def test_backfill_status_includes_runner_progress(client):
    """When a runner is active, /backfill/status surfaces its counters."""
    from algotrader_api.routes import backfill as backfill_route

    class _FakeRunner:
        state = type("S", (), {"value": "backfilling"})()
        tickers_done = 5
        tickers_total = 20
        total_bars = 100

    backfill_route._slot.runner = _FakeRunner()
    backfill_route._slot.run_id = 11
    try:
        r = client.get("/api/admin/backfill/status")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "backfilling"
        assert body["tickers_done"] == 5
        assert body["tickers_total"] == 20
        assert body["total_bars"] == 100
    finally:
        backfill_route._slot.reset()


def test_pending_count_includes_each_branch(tmp_path):
    """Direct unit test for _pending_count's classification branches.

    Covers the `last_status='error'` branch, the
    `last_bar_ts is None` branch, the valid `last_bar_ts` newer than
    threshold (stale), the older (up_to_date), and the
    `last_bar_ts` ValueError fallback.
    """
    import sqlite3 as _sqlite
    from datetime import date, timedelta

    db_path = str(tmp_path / "state.db")
    from algotrader_api.db.sqlite import run_migrations as _run_migrations
    from algotrader_api.db.migrations import MIGRATIONS_DIR as _MD

    _run_migrations(db_path, str(_MD))

    today = date.today().isoformat()
    con = _sqlite.connect(db_path)
    con.executescript(
        f"""
        INSERT INTO instruments (ticker, figi, class, name, currency, lot_size)
        VALUES ('NEW', 'FIGI-NEW', 'share', 'N', 'rub', 1),
               ('ERR', 'FIGI-ERR', 'share', 'E', 'rub', 1),
               ('STALE', 'FIGI-STALE', 'share', 'S', 'rub', 1),
               ('OK', 'FIGI-OK', 'share', 'O', 'rub', 1),
               ('BAD', 'FIGI-BAD', 'share', 'B', 'rub', 1);
        INSERT INTO instrument_metadata
          (figi, last_bar_ts, last_run_status, total_bars)
        VALUES
          ('FIGI-NEW',  NULL,    'ok',     0),
          ('FIGI-ERR',  '2026-01-01', 'error', 0),
          ('FIGI-STALE','2020-01-01', 'ok',     0),
          ('FIGI-OK',   '{today}',  'ok',     100),
          ('FIGI-BAD',  'not-a-date', 'ok', 0);
        """
    )
    con.commit()
    con.close()

    from algotrader_api.routes.backfill import _pending_count

    counts = _pending_count(db_path, incremental_threshold_days=2)
    # FIGI-NEW: new (no last_bar_ts, status ok); FIGI-ERR: error bucket;
    # FIGI-STALE: stale (2020); FIGI-OK: up_to_date (today);
    # FIGI-BAD: new (ValueError fallback).
    assert counts["new"] >= 2  # NEW + BAD
    assert counts["error"] >= 1
    assert counts["stale"] >= 1
    assert counts["up_to_date"] >= 1
    assert counts["total"] == 5


def test_backfill_start_with_fake_token_and_ingest_fake(
    client, fresh_db, monkeypatch
):
    """With ALGOTRADER_INGEST_FAKE=1 and a stub token in the DB, the
    runner kicks off and the route returns 202."""
    import os

    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    con = sqlite3.connect(fresh_db)
    con.executescript(
        """
        INSERT OR REPLACE INTO secrets (key, value, updated_at)
        VALUES ('broker_token', 'fake-test-token', datetime('now'));
        """
    )
    con.commit()
    con.close()

    r = client.post("/api/admin/backfill/start", json={})
    assert r.status_code == 202
    body = r.json()
    assert "run_id" in body
    assert body["state"] == "starting"

    # Let the background task finish before the TestClient tears down
    # (the lifespan stops inside the `with` block).
    import time as time_mod

    time_mod.sleep(0.5)
