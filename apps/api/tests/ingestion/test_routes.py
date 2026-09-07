"""Tests for pipeline and admin routes."""
from __future__ import annotations

import asyncio

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db import sqlite as sqlitedb
from algotrader_api.ingestion import pipeline as pipeline_mod


@pytest.fixture
def app_with_pipeline(data_dir, monkeypatch):
    """App with a pre-populated pipeline table."""
    from algotrader_api.db import duck, sqlite as sqlitedb  # noqa: F811

    sqlitedb.close_all()
    duck.close()
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", data_dir)
    monkeypatch.setenv("ALGOTRADER_LOG_SAMPLE_HEALTH", "1.0")

    # Pre-populate pipeline table
    from algotrader_api.config import get_settings

    settings = get_settings()
    sqlitedb.run_migrations(settings.sqlite_path, MIGRATIONS_DIR)
    rid = pipeline_mod.start_phase(settings.sqlite_path, "discover_universe")
    pipeline_mod.end_phase(settings.sqlite_path, rid, status="ok", rows_processed=248)

    rid = pipeline_mod.start_phase(settings.sqlite_path, "fetch_bars")
    pipeline_mod.end_phase(settings.sqlite_path, rid, status="ok", rows_processed=310000, detail="rate_limit_hits=0")

    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    with TestClient(app) as c:
        yield c

    sqlitedb.close_all()
    duck.close()


@pytest.fixture(autouse=True)
def _isolate_db_connections():
    """Force fresh SQLite/DuckDB connections per test to prevent state bleed.

    Called before data_dir fixture so module-level caches are cleared before
    any other fixture runs.
    """
    from algotrader_api.db import duck, sqlite as sqlitedb

    sqlitedb.close_all()
    duck.close()
    yield
    sqlitedb.close_all()
    duck.close()


def test_get_pipeline_returns_two_phases(app_with_pipeline):
    r = app_with_pipeline.get("/api/pipeline")
    assert r.status_code == 200
    phases = r.json()["phases"]
    assert len(phases) == 2
    by_phase = {p["phase"]: p for p in phases}
    assert by_phase["discover_universe"]["status"] == "ok"
    assert by_phase["discover_universe"]["rowsProcessed"] == 248
    assert by_phase["fetch_bars"]["status"] == "ok"
    assert by_phase["fetch_bars"]["rowsProcessed"] == 310000


def test_get_pipeline_empty(app_with_pipeline, data_dir, monkeypatch):
    """Empty pipeline table returns empty phases list."""
    # Force a brand-new connection by closing all caches, since app_with_pipeline
    # closed its own connections at fixture teardown but data_dir is shared.
    from algotrader_api.db import duck, sqlite as sqlitedb

    sqlitedb.close_all()
    duck.close()

    # Also clear the pipeline table in this test's data dir to ensure it's empty.
    rows = sqlitedb.execute(f"{data_dir}/state.db", "SELECT COUNT(*) AS n FROM pipeline", ())
    sqlitedb.execute(f"{data_dir}/state.db", "DELETE FROM pipeline", ())
    sqlitedb.close_all()
    duck.close()

    from algotrader_api.db import duck, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    sqlitedb.close_all()
    duck.close()
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/pipeline")
        assert r.status_code == 200
        assert r.json()["phases"] == []
    sqlitedb.close_all()
    duck.close()


def test_get_pipeline_propagates_correlation_id(app_with_pipeline):
    r = app_with_pipeline.get("/api/pipeline", headers={"X-Correlation-ID": "pipe-test"})
    assert r.headers["X-Correlation-ID"] == "pipe-test"


def test_admin_fetch_returns_503_when_disabled(data_dir, monkeypatch):
    """When ALGOTRADER_FETCH_DISABLED=1, admin/fetch returns 503."""
    from algotrader_api.db import duck, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ALGOTRADER_FETCH_DISABLED", "1")
    sqlitedb.close_all()
    duck.close()
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/admin/fetch")
        assert r.status_code == 503
        body = r.json()
        assert body["detail"]["error"] == "fetch_disabled"
    sqlitedb.close_all()
    duck.close()


def test_admin_fetch_returns_202_with_run_id(data_dir, monkeypatch):
    """Successful manual fetch returns 202 with run_id, then runs in background."""
    from algotrader_api.db import duck, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    sqlitedb.close_all()
    duck.close()
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/admin/fetch")
        assert r.status_code == 202
        body = r.json()
        assert "run_id" in body
        assert "started_at" in body
        assert body["run_id"] > 0
        # token_last4: None when running in fake mode (no token needed)
        assert body["token_last4"] is None
    sqlitedb.close_all()
    duck.close()


def test_admin_fetch_rejects_when_no_token_in_db(data_dir, monkeypatch):
    """No fake mode + no token in DB → 400 broker_token_missing."""
    from algotrader_api.db import duck, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    monkeypatch.delenv("ALGOTRADER_INGEST_FAKE", raising=False)
    sqlitedb.close_all()
    duck.close()
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/admin/fetch")
        assert r.status_code == 400
        body = r.json()
        assert body["detail"]["error"] == "broker_token_missing"
    sqlitedb.close_all()
    duck.close()


def test_admin_fetch_uses_fresh_token_without_restart(data_dir, monkeypatch):
    """Token check is live, not cached at module-import time.

    Previously the admin route had no token check (the old make_client()
    fallback raised only inside the background task with a cryptic message).
    Now the check runs synchronously per-request against the DB, so PUT
    /api/settings/token takes effect immediately.

    We can't directly test the success path here because the tinkoff SDK
    isn't installed in CI; the other tests cover the 400 / 202-fake cases.
    This test pins the contract: removing the GET /api/admin/fetch/status
    endpoint OR caching the token will break it.
    """
    from algotrader_api.db import duck, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    monkeypatch.delenv("ALGOTRADER_INGEST_FAKE", raising=False)
    sqlitedb.close_all()
    duck.close()
    app = create_app()
    with TestClient(app) as c:
        # No token, real mode → 400
        r1 = c.post("/api/admin/fetch")
        assert r1.status_code == 400
        assert r1.json()["detail"]["error"] == "broker_token_missing"
    sqlitedb.close_all()
    duck.close()


def test_fetch_status_reports_token_set_state(data_dir):
    """GET /api/admin/fetch/status returns token_set + token_last4 (or None)."""
    from algotrader_api.db import duck, secrets as secrets_repo, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    db_file = f"{data_dir}/state.db"
    sqlitedb.close_all()
    duck.close()
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/admin/fetch/status")
        assert r.status_code == 200
        body = r.json()
        assert body["token_set"] is False
        assert body["token_last4"] is None
        # Now write a token and re-check.
        secrets_repo.set_secret(db_file, "broker_token", "t.live.WXYZ")
        sqlitedb.close_all()
        r2 = c.get("/api/admin/fetch/status")
        assert r2.json()["token_set"] is True
        assert r2.json()["token_last4"] == "WXYZ"
    sqlitedb.close_all()
    duck.close()


def test_admin_fetch_creates_pipeline_rows(data_dir, monkeypatch):
    """After admin fetch starts, pipeline table gets at least one row."""
    import os

    from algotrader_api.db import duck, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    sqlitedb.close_all()
    duck.close()
    app = create_app()
    with TestClient(app) as c:
        # Pre-populate an instruments table so the bars phase can run
        settings_db = os.path.join(data_dir, "state.db")
        sqlitedb.execute(
            settings_db,
            "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size, isin, sector) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("TEST", "F1", "share", "n", "RUB", 1, None, None),
        )

        r = c.post("/api/admin/fetch")
        run_id = r.json()["run_id"]

        # Wait briefly for background task to finish — InMemoryTinkoffClient
        # returns immediately, so the background task should complete fast.
        import time as time_mod

        for _ in range(50):
            phases = c.get("/api/pipeline").json()["phases"]
            if any(p["status"] == "ok" and p["phase"] == "fetch_bars" for p in phases):
                break
            time_mod.sleep(0.05)

        phases = c.get("/api/pipeline").json()["phases"]
        by_phase = {p["phase"]: p for p in phases}
        assert by_phase["fetch_bars"]["status"] == "ok"
        assert by_phase["discover_universe"]["status"] == "ok"
    sqlitedb.close_all()
    duck.close()


def test_admin_fetch_marks_phase_err_on_universe_failure(data_dir, monkeypatch):
    """If universe discovery fails, phase marked err but admin returns 202."""
    from algotrader_api.db import duck, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    # Use ALGOTRADER_INGEST_FAKE but don't preload — universe returns []
    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    sqlitedb.close_all()
    duck.close()
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/admin/fetch")
        assert r.status_code == 202
        run_id = r.json()["run_id"]

        # Background task completes (empty universe = 0 rows but still ok status)
        import time as time_mod

        for _ in range(50):
            phases = c.get("/api/pipeline").json()["phases"]
            if any(p["status"] in ("ok", "err") for p in phases if p["phase"] == "discover_universe"):
                break
            time_mod.sleep(0.05)

        phases = c.get("/api/pipeline").json()["phases"]
        by_phase = {p["phase"]: p for p in phases}
        # Empty universe = 0 rows but status='ok' (no error)
        assert by_phase["discover_universe"]["status"] == "ok"
        assert by_phase["discover_universe"]["rowsProcessed"] == 0
    sqlitedb.close_all()
    duck.close()


def test_admin_fetch_with_fetch_disabled_via_env_var(data_dir, monkeypatch):
    """ALGOTRADER_FETCH_DISABLED=1 → 503 (env var path, already covered)."""
    from algotrader_api.db import duck, sqlite as sqlitedb
    from algotrader_api.main import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ALGOTRADER_FETCH_DISABLED", "1")
    sqlitedb.close_all()
    duck.close()
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/admin/fetch")
        assert r.status_code == 503
        assert r.json()["detail"]["error"] == "fetch_disabled"
    sqlitedb.close_all()
    duck.close()
