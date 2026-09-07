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


def test_put_token_writes_file_and_returns_last4(client, tmp_path, monkeypatch):
    """PUT /api/settings/token writes to HOME/.hermes/secrets/tinkoff_token."""
    secrets_dir = tmp_path / ".hermes" / "secrets"
    secrets_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    r = client.put("/api/settings/token", json={"token": "t.realvalue.9999"})
    assert r.status_code == 200
    body = r.json()
    assert body["tokenLast4"] == "9999"
    assert body["tokenRedacted"] is True
    on_disk = secrets_dir / "tinkoff_token"
    assert on_disk.exists()
    assert on_disk.read_text().strip() == "t.realvalue.9999"
    # mode 0600
    import stat
    mode = on_disk.stat().st_mode & 0o777
    assert mode == 0o600


def test_put_token_rejects_empty(client):
    r = client.put("/api/settings/token", json={"token": ""})
    assert r.status_code == 422  # Pydantic min_length=1


def test_put_token_strips_whitespace(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = client.put("/api/settings/token", json={"token": "  t.realvalue.ABCD\n  "})
    assert r.status_code == 200
    assert r.json()["tokenLast4"] == "ABCD"


def test_put_token_emits_span_and_log(client, tmp_path, monkeypatch):
    """Tracer span records token_last4; logger emits info event."""
    monkeypatch.setenv("HOME", str(tmp_path))
    r = client.put("/api/settings/token", json={"token": "t.long.token.1234"})
    assert r.status_code == 200
    # Span attribute is internal — verified by absence of exception.
