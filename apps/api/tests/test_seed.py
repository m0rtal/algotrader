"""Tests for should_seed_synth() in seed module."""
from __future__ import annotations

import os

from algotrader_api.seed import should_seed_synth


def test_returns_true_when_token_file_missing(tmp_path, monkeypatch):
    """Default behavior: if token file doesn't exist, fall back to synth seed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Token file at ~/.hermes/secrets/tinkoff_token does not exist
    assert should_seed_synth() is True


def test_returns_false_when_token_file_empty_but_readable(tmp_path, monkeypatch):
    """Empty but readable file: should_seed_synth only checks existence+access,
    not content. Token validation happens at read_token_file() time."""
    monkeypatch.setenv("HOME", str(tmp_path))
    secrets = tmp_path / ".hermes" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "tinkoff_token").write_text("   \n")
    # File exists + is readable → should_seed_synth returns False
    # (token validation is a separate concern)
    assert should_seed_synth() is False


def test_returns_false_when_token_file_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    secrets = tmp_path / ".hermes" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "tinkoff_token").write_text("t.real_token_here")
    monkeypatch.delenv("ALGOTRADER_SYNTH_SEED", raising=False)
    assert should_seed_synth() is False


def test_returns_true_when_explicit_env_override(tmp_path, monkeypatch):
    """ALGOTRADER_SYNTH_SEED=1 forces synth even when token file is present."""
    monkeypatch.setenv("HOME", str(tmp_path))
    secrets = tmp_path / ".hermes" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "tinkoff_token").write_text("t.real_token_here")
    monkeypatch.setenv("ALGOTRADER_SYNTH_SEED", "1")
    assert should_seed_synth() is True


def test_returns_false_when_env_unset_and_token_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    secrets = tmp_path / ".hermes" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "tinkoff_token").write_text("t.real_token_here")
    monkeypatch.setenv("ALGOTRADER_SYNTH_SEED", "")
    assert should_seed_synth() is False


def test_custom_token_path_is_respected(tmp_path):
    custom = tmp_path / "custom_token"
    custom.write_text("t.custom")
    assert should_seed_synth(str(custom)) is False
    assert should_seed_synth(str(tmp_path / "missing")) is True
