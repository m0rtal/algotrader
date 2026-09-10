"""Coverage tests for routes/settings.py — error paths."""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from algotrader_api.main import create_app
from algotrader_api.routes import settings as settings_routes


@pytest.fixture
def isolated_client(tmp_path, monkeypatch):
    """TestClient for settings routes that writes to tmp_path, not live DB.

    Plain `create_app()` reads `ALGOTRADER_DATA_DIR` from env, falling back
    to `./data`. Without this fixture, tests would silently upsert rows into
    the operator's running app database (token placeholder writes, etc.).
    """
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path))
    # Also flush the global sqlite-path holder so lifespan doesn't leak in
    # a previous test's tmp_path between runs.
    settings_routes._sqlite_path_holder.clear()
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_settings_token_empty_token_returns_400(isolated_client):
    r = isolated_client.put("/api/settings/token", json={"token": "   "})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "empty_token"


def test_settings_token_write_failure_returns_500(isolated_client):
    with patch("algotrader_api.routes.settings.set_secret", side_effect=RuntimeError("db locked")):
        r = isolated_client.put("/api/settings/token", json={"token": "t.real.ABCD"})
    assert r.status_code == 500
    assert r.json()["detail"]["error"] == "token_write_failed"


def test_settings_token_put_success_returns_redacted_token(isolated_client):
    r = isolated_client.put("/api/settings/token", json={"token": "t.real.WXYZ"})
    assert r.status_code == 200
    body = r.json()
    assert body["tokenLast4"] == "WXYZ"
    assert body["tokenRedacted"] is True


def test_get_sqlite_path_raises_when_not_configured():
    settings_routes._sqlite_path_holder.clear()
    with pytest.raises(RuntimeError, match="sqlite path not configured"):
        settings_routes._get_sqlite_path()
