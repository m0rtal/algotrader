"""Tests for the TinkoffClient Protocol + read_token_file + make_client factory."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from algotrader_api.ingestion.client import TinkoffClient, make_client, read_token_file
from algotrader_api.ingestion.fake_client import InMemoryTinkoffClient


def test_read_token_file_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # No token file exists
    assert read_token_file("~/.hermes/secrets/nonexistent_token") is None


def test_read_token_file_returns_none_when_unreadable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "locked_token"
    f.write_text("t.fake")
    os.chmod(f, 0o000)
    # We can't actually make a file unreadable to root in many envs, so this
    # test may be skipped. Skip if running as root.
    if os.geteuid() == 0:
        pytest.skip("running as root, chmod 0o000 ineffective")
    assert read_token_file(str(f)) is None


def test_read_token_file_returns_content_when_valid(tmp_path):
    f = tmp_path / "token"
    f.write_text("t.fake_token_for_test\n")  # trailing newline should be stripped
    assert read_token_file(str(f)) == "t.fake_token_for_test"


def test_read_token_file_returns_none_for_empty_file(tmp_path):
    f = tmp_path / "empty_token"
    f.write_text("   \n")
    assert read_token_file(str(f)) is None


def test_read_token_file_returns_none_on_oserror(tmp_path, monkeypatch):
    """If read_text raises OSError (e.g., disk error), return None."""
    f = tmp_path / "broken_token"
    f.write_text("t.fake")
    # Patch the Path.read_text on this specific file to raise
    from pathlib import Path as PathCls

    def broken_read_text(self, *args, **kwargs):
        raise OSError("disk error simulation")

    monkeypatch.setattr(PathCls, "read_text", broken_read_text)
    assert read_token_file(str(f)) is None


def test_make_client_with_fake_env_returns_fake(monkeypatch):
    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    client = make_client()
    assert isinstance(client, InMemoryTinkoffClient)


def test_make_client_with_token_file_returns_real(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("t.fake_test_token")
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path))  # so config picks up
    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "")  # ensure not fake
    # Use the explicit token_path arg so we don't depend on HOME expansion
    # Move the token file to the expected location
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".hermes" / "secrets").mkdir(parents=True)
    (tmp_path / ".hermes" / "secrets" / "tinkoff_token").write_text("t.fake")

    # Patch to prevent RealTinkoffClient from actually trying to instantiate SDK
    with patch("algotrader_api.ingestion.real_client.RealTinkoffClient") as mock_cls:
        mock_cls.return_value = "real-client-stub"
        client = make_client()
        # Real path was taken, but our mock stands in
        assert mock_cls.called


def test_read_token_file_returns_none_on_oserror(tmp_path, monkeypatch):
    """OSError on read_text → None + log warning."""
    from unittest.mock import patch
    f = tmp_path / "tok"
    f.write_text("data")
    monkeypatch.setattr("pathlib.Path.read_text", lambda self, **kw: (_ for _ in ()).throw(OSError("disk error")))
    assert read_token_file(str(f)) is None


def test_read_token_file_returns_none_when_unreadable(tmp_path):
    """File exists but os.access R_OK is False → None."""
    f = tmp_path / "tok"
    f.write_text("data")
    f.chmod(0o000)
    import stat
    # On some systems root can still read; check that access returns False
    import os
    if not os.access(f, os.R_OK):
        assert read_token_file(str(f)) is None
    else:
        # If running as root, skip — defensive branch unreachable in this env
        import pytest
        pytest.skip("running as root, cannot test unreadable file")


def test_read_token_file_strips_whitespace(tmp_path):
    f = tmp_path / "tok"
    f.write_text("  t.realval.ABCD\n")
    assert read_token_file(str(f)) == "t.realval.ABCD"


def test_make_client_raises_when_token_missing_and_not_fake(tmp_path, monkeypatch):
    """No token file + not fake → RuntimeError."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ALGOTRADER_INGEST_FAKE", raising=False)
    with pytest.raises(RuntimeError, match="Tinkoff token not found"):
        make_client(use_fake=False)


def test_make_client_returns_fake_when_use_fake_true():
    from algotrader_api.ingestion.fake_client import InMemoryTinkoffClient
    c = make_client(use_fake=True)
    assert isinstance(c, InMemoryTinkoffClient)
