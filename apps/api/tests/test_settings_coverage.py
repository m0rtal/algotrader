"""Coverage tests for routes/settings.py — error paths."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from algotrader_api.main import create_app
from algotrader_api.routes import settings as settings_routes


def _client(tmp_path):
    db = tmp_path / "state.db"
    settings_routes.set_sqlite_path(str(db))
    app = create_app()
    return TestClient(app)


def test_settings_token_empty_token_returns_400(tmp_path):
    with _client(tmp_path) as c:
        r = c.put("/api/settings/token", json={"token": "   "})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "empty_token"


def test_settings_token_write_failure_returns_500(tmp_path):
    with _client(tmp_path) as c, \
         patch("algotrader_api.routes.settings.set_secret", side_effect=RuntimeError("db locked")):
        r = c.put("/api/settings/token", json={"token": "t.real.ABCD"})
    assert r.status_code == 500
    assert r.json()["detail"]["error"] == "token_write_failed"


def test_settings_token_put_success_returns_redacted_token(tmp_path):
    with _client(tmp_path) as c:
        r = c.put("/api/settings/token", json={"token": "t.real.WXYZ"})
    assert r.status_code == 200
    body = r.json()
    assert body["tokenLast4"] == "WXYZ"
    assert body["tokenRedacted"] is True


def test_get_sqlite_path_raises_when_not_configured():
    settings_routes._sqlite_path_holder.clear()
    with pytest.raises(RuntimeError, match="sqlite path not configured"):
        settings_routes._get_sqlite_path()
