"""Pytest fixtures: app, TestClient, fresh temp dirs per test."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Fresh data dir per test; env vars override default settings."""
    d = tmp_path / "data"
    d.mkdir()
    bars_dir = d / "bars"
    bars_dir.mkdir()
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(d))
    monkeypatch.setenv("ALGOTRADER_LOG_SAMPLE_HEALTH", "1.0")  # no sampling in tests
    # Force synth seed in tests: redirect HOME to an empty dir so
    # should_seed_synth() sees no token file and falls back to seed_bars.
    monkeypatch.setenv("HOME", str(tmp_path))
    return str(d)


@pytest.fixture
def client(data_dir):
    """TestClient with lifespan started."""
    # Reset module-level cache for sqlite & duck connections
    from algotrader_api.db import duck, sqlite as sqlitedb

    sqlitedb.close_all()
    duck.close()

    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c

    sqlitedb.close_all()
    duck.close()


@pytest.fixture
def fresh_db(data_dir):
    """Just the path, no app — for unit-testing DB layer in isolation."""
    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.db.migrations import MIGRATIONS_DIR

    sqlitedb.close_all()
    sqlitedb.run_migrations(f"{data_dir}/state.db", MIGRATIONS_DIR)
    yield f"{data_dir}/state.db"
    sqlitedb.close_all()
