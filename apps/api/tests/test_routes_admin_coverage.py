"""Targeted coverage tests for ``routes/admin.py`` (issue #64).

Each test exercises a specific line range reported as missed by
``pytest --cov-report=term-missing``.
"""
from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations


@pytest.fixture
def db_path(tmp_path):
    """Run migrations on a fresh DB; yield the path."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    yield p


# ─── data-pipeline/status: freshness generic-exception path (lines 93-94) ───


def test_data_pipeline_status_freshness_falls_back_on_unexpected_error(
    client, fresh_db, monkeypatch
):
    """Lines 93-94: when ``pipeline_freshness_check`` raises something
    other than ``AssertionError`` (e.g. a RuntimeError), the route
    returns ``freshness`` with the empty-buckets shape instead of
    500-ing."""
    from algotrader_api.routes import admin as admin_route
    from algotrader_api.dividends import freshness as freshness_mod

    def boom(db_path, max_chain_age_hours=24):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(freshness_mod, "pipeline_freshness_check", boom)
    # Also patch the re-imported reference inside the route module
    # (the route does `from ..dividends.freshness import pipeline_freshness_check`)
    monkeypatch.setattr(
        admin_route, "pipeline_freshness_check", boom, raising=False
    )

    r = client.get("/api/admin/data-pipeline/status")
    assert r.status_code == 200
    body = r.json()
    assert "freshness" in body
    assert body["freshness"] == {"bars": {}, "dividends": {}, "corporate_actions": {}}


def test_data_pipeline_status_handles_assertion_error_from_freshness(
    client, fresh_db, monkeypatch
):
    """Lines 91-92: ``AssertionError`` from pipeline_freshness_check is
    caught and converted to ``{"stale": True, "reason": ...}``."""
    from algotrader_api.routes import admin as admin_route

    def boom_assert(db_path, max_chain_age_hours=24):
        raise AssertionError("pipeline_log table missing (simulated)")

    monkeypatch.setattr(
        admin_route, "pipeline_freshness_check", boom_assert, raising=False
    )

    r = client.get("/api/admin/data-pipeline/status")
    assert r.status_code == 200
    body = r.json()
    assert body["freshness"]["stale"] is True
    assert "pipeline_log" in body["freshness"]["reason"]


# ─── trigger_fetch / _run_phases: no-token early return (lines 184-187) ─────


def test_trigger_fetch_no_token_uses_fake_ingest(monkeypatch):
    """When ALGOTRADER_INGEST_FAKE=1, /admin/fetch returns 202 without
    needing a broker token — the ALGOTRADER_INGEST_FAKE short-circuit
    avoids the missing-token branch. This test pins down that happy path
    so the no-token path remains distinguishable in coverage."""
    import os

    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    monkeypatch.delenv("ALGOTRADER_FETCH_DISABLED", raising=False)

    # Use the lifespan-bearing test client via the conftest's `client` fixture.
    # But we need our own client here so monkeypatch env is set first.
    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    sqlitedb.close_all()
    with TestClient(create_app()) as c:
        r = c.post("/api/admin/fetch")
        assert r.status_code == 202
        assert "run_id" in r.json()
    sqlitedb.close_all()


def test_trigger_fetch_returns_503_when_fetch_disabled(monkeypatch):
    """Sanity: when ALGOTRADER_FETCH_DISABLED=true, the route returns 503
    rather than kicking off the pipeline."""
    monkeypatch.setenv("ALGOTRADER_FETCH_DISABLED", "true")

    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    sqlitedb.close_all()
    with TestClient(create_app()) as c:
        r = c.post("/api/admin/fetch")
        assert r.status_code == 503
        assert r.json()["detail"]["error"] == "fetch_disabled"
    sqlitedb.close_all()


def test_run_phases_no_token_returns_early_directly(tmp_path, fresh_db, monkeypatch):
    """Lines 184-187: when ``_run_phases`` runs WITHOUT a token (not
    fake-ingest) it records an ``err`` phase via ``pipeline.end_phase``
    and returns early. The route layer checks the token synchronously
    in ``trigger_fetch`` and would normally return 400 before this
    branch fires, so we call ``_run_phases`` directly.
    """
    from algotrader_api.db import sqlite as sqlitedb
    monkeypatch.delenv("ALGOTRADER_INGEST_FAKE", raising=False)

    from algotrader_api.routes import admin as admin_route
    from algotrader_api.ingestion import pipeline as pipeline_mod

    sqlitedb.close_all()
    # Ensure no broker_token row exists (fresh_db fixture guarantees this).
    # Pre-create a pipeline row so end_phase's UPDATE has a target.
    run_id = pipeline_mod.start_phase(fresh_db, "discover_universe")

    import asyncio as _asyncio
    _asyncio.run(admin_route._run_phases(run_id=run_id, db_path=fresh_db))

    with sqlite3.connect(fresh_db) as con:
        row = con.execute(
            "SELECT status, detail FROM pipeline WHERE id = ?", (run_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "err"
    assert "broker_token_missing" in (row[1] or "")


def test_run_phases_client_aclose_failure_is_silent(tmp_path, monkeypatch):
    """Lines 224-225: when ``client.aclose()`` raises during the finally
    cleanup, the exception is swallowed so the worker doesn't crash."""
    from algotrader_api.db import sqlite as sqlitedb

    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path))
    (tmp_path / "bars").mkdir(exist_ok=True)
    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    monkeypatch.delenv("ALGOTRADER_FETCH_DISABLED", raising=False)
    monkeypatch.setenv("ALGOTRADER_LOG_SAMPLE_HEALTH", "1.0")

    db_path = str(tmp_path / "state.db")
    sqlitedb.run_migrations(
        db_path,
        str(__import__("pathlib").Path(__file__).resolve().parent.parent
            / "src/algotrader_api/db/migrations"),
    )
    sqlitedb.close_all()

    # Build a fake client whose aclose() raises so the except arm fires.
    fake_client = MagicMock()
    fake_client.aclose = AsyncMock(side_effect=RuntimeError("aclose boom"))

    # Build a fake runner whose run() returns successfully.
    fake_runner = MagicMock()
    fake_runner.run = AsyncMock(return_value=None)
    fake_runner.total_bars = 0

    from algotrader_api.ingestion import client as client_mod
    from algotrader_api.ingestion.backfill import BackfillRunner

    monkeypatch.setattr(client_mod, "make_client", lambda **kw: fake_client)
    monkeypatch.setattr(
        BackfillRunner,
        "__init__",
        lambda self, client, db_path, event_sink: setattr(
            self, "client", client
        )
        or setattr(self, "db_path", db_path)
        or setattr(self, "event_sink", event_sink)
        or setattr(self, "run_id", 0),
    )
    monkeypatch.setattr(BackfillRunner, "run", fake_runner.run)

    from algotrader_api.routes import admin as admin_route
    from algotrader_api.ingestion import pipeline as pipeline_mod
    import asyncio as _asyncio

    sqlitedb.close_all()
    run_id = pipeline_mod.start_phase(db_path, "discover_universe")
    # Should NOT raise even though aclose() raises.
    _asyncio.run(admin_route._run_phases(run_id=run_id, db_path=db_path))

    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT status FROM pipeline WHERE id = ?", (run_id,)
        ).fetchone()
    assert row is not None
    # Pipeline phase ends 'ok' since the runner finished cleanly; the
    # aclose failure was swallowed.
    assert row[0] == "ok"
    sqlitedb.close_all()


def test_run_phases_runner_failure_caught(tmp_path, monkeypatch):
    """Lines 219-220: when the runner raises during ``run()``, the
    except arm records an ``err`` phase rather than crashing the worker.
    """
    from algotrader_api.db import sqlite as sqlitedb

    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path))
    (tmp_path / "bars").mkdir(exist_ok=True)
    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    monkeypatch.delenv("ALGOTRADER_FETCH_DISABLED", raising=False)
    monkeypatch.setenv("ALGOTRADER_LOG_SAMPLE_HEALTH", "1.0")

    db_path = str(tmp_path / "state.db")
    sqlitedb.run_migrations(
        db_path,
        str(__import__("pathlib").Path(__file__).resolve().parent.parent
            / "src/algotrader_api/db/migrations"),
    )
    sqlitedb.close_all()

    fake_client = MagicMock()
    fake_client.aclose = AsyncMock(return_value=None)

    fake_runner = MagicMock()
    fake_runner.run = AsyncMock(side_effect=RuntimeError("runner crashed"))
    fake_runner.total_bars = 0

    from algotrader_api.ingestion import client as client_mod
    from algotrader_api.ingestion.backfill import BackfillRunner

    monkeypatch.setattr(client_mod, "make_client", lambda **kw: fake_client)
    monkeypatch.setattr(
        BackfillRunner,
        "__init__",
        lambda self, client, db_path, event_sink: setattr(
            self, "client", client
        )
        or setattr(self, "db_path", db_path)
        or setattr(self, "event_sink", event_sink)
        or setattr(self, "run_id", 0),
    )
    monkeypatch.setattr(BackfillRunner, "run", fake_runner.run)

    from algotrader_api.routes import admin as admin_route
    from algotrader_api.ingestion import pipeline as pipeline_mod
    import asyncio as _asyncio

    sqlitedb.close_all()
    run_id = pipeline_mod.start_phase(db_path, "discover_universe")
    _asyncio.run(admin_route._run_phases(run_id=run_id, db_path=db_path))

    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT status, detail FROM pipeline WHERE id = ?", (run_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "err"
    assert "runner crashed" in (row[1] or "")
    sqlitedb.close_all()


def test_run_phases_sdk_init_failure(tmp_path, monkeypatch):
    """Lines 219-220: when ``make_client`` raises RuntimeError (SDK
    import failed), the background task records an `err` phase rather
    than crashing the worker.
    """
    from algotrader_api.db import sqlite as sqlitedb

    # Point data_dir at tmp_path so we know the DB location.
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path))
    (tmp_path / "bars").mkdir(exist_ok=True)
    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    monkeypatch.delenv("ALGOTRADER_FETCH_DISABLED", raising=False)
    monkeypatch.setenv("ALGOTRADER_LOG_SAMPLE_HEALTH", "1.0")

    db_path = str(tmp_path / "state.db")
    # Pre-create schema (the `pipeline` table is from migrations).
    sqlitedb.run_migrations(db_path, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src/algotrader_api/db/migrations"))
    sqlitedb.close_all()

    # Patch make_client to raise RuntimeError so the runner's client
    # init branch fires (line 219-220).
    from algotrader_api.ingestion import client as client_mod

    def boom_make_client(sqlite_path, use_fake=False, target=None):
        raise RuntimeError("simulated SDK init failure")

    monkeypatch.setattr(client_mod, "make_client", boom_make_client)

    from algotrader_api.routes import admin as admin_route
    from algotrader_api.ingestion import pipeline as pipeline_mod
    import asyncio as _asyncio

    sqlitedb.close_all()
    # Pre-create a pipeline row so end_phase's UPDATE has a target.
    run_id = pipeline_mod.start_phase(db_path, "discover_universe")

    # Call _run_phases directly so we don't depend on the route layer.
    # With ALGOTRADER_INGEST_FAKE=1 the token check is skipped (line 184).
    # make_client raises → caught at line 219, recorded as err phase.
    _asyncio.run(admin_route._run_phases(run_id=run_id, db_path=db_path))

    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT status, detail FROM pipeline WHERE id = ?", (run_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "err"
    assert "SDK init failure" in (row[1] or "")
    sqlitedb.close_all()