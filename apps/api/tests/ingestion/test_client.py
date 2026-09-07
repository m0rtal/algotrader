"""Tests for the TinkoffClient Protocol + load_broker_token + make_client factory."""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from algotrader_api.db.secrets import set_secret
from algotrader_api.ingestion.client import (
    TinkoffClient,
    load_broker_token,
    make_client,
)


def _seed_db(tmp_path, value: str = ""):
    """Create a fresh SQLite state.db and return its path."""
    import sqlite3
    db = tmp_path / "state.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE secrets (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    if value:
        con.execute("INSERT INTO secrets (key, value, updated_at) VALUES ('broker_token', ?, datetime('now'))", (value,))
    con.commit()
    con.close()
    return str(db)


def test_load_broker_token_returns_none_when_db_missing(tmp_path):
    assert load_broker_token(str(tmp_path / "nope.db")) is None


def test_load_broker_token_returns_none_when_row_empty(tmp_path):
    db = _seed_db(tmp_path, value="")
    assert load_broker_token(db) is None


def test_load_broker_token_returns_value_when_set(tmp_path):
    db = _seed_db(tmp_path, value="t.real.ABCD")
    assert load_broker_token(db) == "t.real.ABCD"


def test_load_broker_token_uses_env_var_when_no_path(tmp_path, monkeypatch):
    db = _seed_db(tmp_path, value="t.env.val.9999")
    monkeypatch.setenv("ALGOTRADER_SQLITE_PATH", db)
    assert load_broker_token() == "t.env.val.9999"


def test_load_broker_token_returns_none_when_sqlite_path_unset(monkeypatch):
    monkeypatch.delenv("ALGOTRADER_SQLITE_PATH", raising=False)
    assert load_broker_token() is None


def test_load_broker_token_logs_no_token_value(tmp_path):
    """Token never appears in logs — only length matters."""
    db = _seed_db(tmp_path, value="t.real.ABCD")
    with patch("algotrader_api.ingestion.client.logger") as mock_log:
        token = load_broker_token(db)
    assert token == "t.real.ABCD"
    # Confirm no call passes the value
    for call in mock_log.info.call_args_list:
        assert "t.real.ABCD" not in str(call)


def test_make_client_returns_fake_when_use_fake_true():
    from algotrader_api.ingestion.fake_client import InMemoryTinkoffClient
    c = make_client(use_fake=True)
    assert isinstance(c, InMemoryTinkoffClient)


def test_make_client_raises_when_no_token_in_db(tmp_path):
    """No token row + not fake → RuntimeError."""
    db = _seed_db(tmp_path, value="")
    with pytest.raises(RuntimeError, match="broker token not set"):
        make_client(use_fake=False, sqlite_path=db)


def test_make_client_returns_real_when_token_in_db(tmp_path):
    """make_client(use_fake=False) builds a RealTinkoffClient when token present.

    SDK isn't installed in CI — so the constructor raises immediately. We just
    assert that the factory attempts the real path (it gets past token lookup
    before failing on SDK import).
    """
    db = _seed_db(tmp_path, value="t.real.ABCD")
    # Patch RealTinkoffClient where it's looked up (lazy import inside make_client)
    with patch("algotrader_api.ingestion.real_client.RealTinkoffClient") as MockClient:
        MockClient.return_value = "mocked-instance"
        c = make_client(use_fake=False, sqlite_path=db)
    assert c == "mocked-instance"
    MockClient.assert_called_once_with(token="t.real.ABCD")


def test_make_client_uses_env_var_for_fake_flag(monkeypatch):
    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    from algotrader_api.ingestion.fake_client import InMemoryTinkoffClient
    c = make_client()
    assert isinstance(c, InMemoryTinkoffClient)


def test_tinkoff_client_protocol_accepts_fake():
    """Protocol runtime check — fake implements all required methods."""
    from algotrader_api.ingestion.fake_client import InMemoryTinkoffClient
    c = InMemoryTinkoffClient()
    assert isinstance(c, TinkoffClient)


def test_set_secret_then_load_via_factory(tmp_path):
    """End-to-end: write token via db.secrets → factory attempts real client."""
    db = _seed_db(tmp_path, value="")
    set_secret(db, "broker_token", "t.written.1234")
    with patch("algotrader_api.ingestion.real_client.RealTinkoffClient") as MockClient:
        MockClient.return_value = "mocked"
        c = make_client(use_fake=False, sqlite_path=db)
    assert c == "mocked"
    MockClient.assert_called_once_with(token="t.written.1234")


def test_load_broker_token_handles_db_error(tmp_path, monkeypatch):
    """DB raises → load_broker_token returns None + logs warning."""
    db = tmp_path / "broken.db"
    db.write_text("not a sqlite db")
    with patch("algotrader_api.ingestion.client.get_broker_token", side_effect=sqlite3.DatabaseError("corrupt")):
        assert load_broker_token(str(db)) is None
