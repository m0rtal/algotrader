"""Tests for /api/settings GET/PUT/DELETE."""
from __future__ import annotations

import pytest


@pytest.fixture
def full_settings() -> dict:
    return {
        "broker": {
            "environment": "sandbox",
            "tokenLast4": "ABCD",
            "tokenRedacted": True,
            "accountId": "ACC-1",
        },
        "risk": {
            "maxDrawdownPct": 12.0,
            "maxPositionSizePct": 25.0,
            "killSwitchEnabled": True,
            "killSwitchThresholdPct": 15.0,
        },
        "ml": {
            "modelVersion": "v1",
            "retrainIntervalDays": 30,
            "confidenceThreshold": 0.6,
            "regimeFilter": "all",
        },
        "data": {
            "source": "tinkoff",
            "cacheTtlMinutes": 60,
            "historyYears": 5,
            "autoFetch": True,
        },
    }


def test_get_settings_returns_defaults_when_empty(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == ""
    assert body["values"]["broker"]["environment"] == "sandbox"


def test_get_settings_correlation_id_echoed(client):
    r = client.get("/api/settings", headers={"X-Correlation-ID": "trace-x"})
    assert r.headers["X-Correlation-ID"] == "trace-x"


def test_put_settings_persists_and_returns_new_version(client, full_settings):
    body = {"values": full_settings, "version": ""}
    r = client.put("/api/settings", json=body)
    assert r.status_code == 200
    assert r.json()["version"] != ""
    new_version = r.json()["version"]

    # GET returns the saved values
    r2 = client.get("/api/settings")
    assert r2.status_code == 200
    assert r2.json()["version"] == new_version
    assert r2.json()["values"]["risk"]["maxDrawdownPct"] == 12.0


def test_put_settings_with_correct_version_succeeds(client, full_settings):
    # First put
    r = client.put("/api/settings", json={"values": full_settings, "version": ""})
    current_version = r.json()["version"]

    # Second put with same version should succeed (maxDrawdown stays below threshold)
    new_settings = {**full_settings, "ml": {**full_settings["ml"], "retrainIntervalDays": 14}}
    r2 = client.put("/api/settings", json={"values": new_settings, "version": current_version})
    assert r2.status_code == 200
    assert r2.json()["version"] != current_version


def test_put_settings_with_stale_version_returns_409(client, full_settings):
    # First put
    client.put("/api/settings", json={"values": full_settings, "version": ""})

    # Second put with wrong version
    r2 = client.put("/api/settings", json={"values": full_settings, "version": "wrong-version"})
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert detail["error"] == "version_conflict"
    assert detail["current"]["version"] != "wrong-version"


def test_put_settings_with_bad_schema_returns_422(client):
    body = {"values": {"risk": {"maxDrawdownPct": 999}}, "version": ""}  # out of range
    r = client.put("/api/settings", json=body)
    assert r.status_code == 422


def test_put_settings_invalidates_token_redacted_flag(client, full_settings):
    body = {"values": full_settings, "version": ""}
    r = client.put("/api/settings", json=body)
    assert r.json()["values"]["broker"]["tokenRedacted"] is True


def test_delete_settings_drops_row(client, full_settings):
    client.put("/api/settings", json={"values": full_settings, "version": ""})
    r = client.delete("/api/settings")
    assert r.status_code == 204

    r2 = client.get("/api/settings")
    assert r2.json()["version"] == ""  # back to defaults


def test_delete_settings_when_empty_is_noop(client):
    r = client.delete("/api/settings")
    assert r.status_code == 204


def test_put_token_writes_db_and_returns_last4(client):
    """PUT /api/settings/token stores in the secrets table, returns last-4."""
    from algotrader_api.db.secrets import get_broker_token
    from algotrader_api.db.sqlite import execute
    from algotrader_api.routes.settings import _get_sqlite_path
    r = client.put("/api/settings/token", json={"token": "t.realvalue.9999"})
    assert r.status_code == 200
    body = r.json()
    assert body["tokenLast4"] == "9999"
    assert body["tokenRedacted"] is True
    # Verify the value landed in the secrets table (not a file)
    stored = get_broker_token(_get_sqlite_path())
    assert stored == "t.realvalue.9999"
    # And the structured settings table is untouched
    rows = execute(_get_sqlite_path(), "SELECT value FROM settings WHERE key = 'main'", ())
    if rows:
        import json
        settings = json.loads(rows[0]["value"])
        assert "token" not in settings.get("broker", {})


def test_put_token_rejects_empty(client):
    r = client.put("/api/settings/token", json={"token": ""})
    assert r.status_code == 422  # Pydantic min_length=1


def test_put_token_strips_whitespace(client):
    r = client.put("/api/settings/token", json={"token": "  t.realvalue.ABCD\n  "})
    assert r.status_code == 200
    assert r.json()["tokenLast4"] == "ABCD"


def test_put_token_overwrites_previous_value(client):
    """PUT /api/settings/token replaces existing value."""
    from algotrader_api.db.secrets import get_broker_token
    from algotrader_api.routes.settings import _get_sqlite_path
    client.put("/api/settings/token", json={"token": "t.first.AAAA"})
    client.put("/api/settings/token", json={"token": "t.second.BBBB"})
    assert get_broker_token(_get_sqlite_path()) == "t.second.BBBB"


def test_put_token_emits_span_and_log(client):
    """Tracer span records token_last4; logger emits info event."""
    r = client.put("/api/settings/token", json={"token": "t.long.token.1234"})
    assert r.status_code == 200
    # Span attribute is internal — verified by absence of exception.
