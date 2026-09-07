"""Tests for should_seed_synth() — now checks the broker_token row in secrets."""
from __future__ import annotations

import os
import sqlite3

from algotrader_api.seed import should_seed_synth


def _fresh_db(tmp_path, value: str = "") -> str:
    """Build a state.db with the secrets table — the surface should_seed_synth reads."""
    db = tmp_path / "state.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE secrets (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    if value:
        con.execute(
            "INSERT INTO secrets (key, value, updated_at) VALUES ('broker_token', ?, datetime('now'))",
            (value,),
        )
    con.commit()
    con.close()
    return str(db)


def test_returns_true_when_db_missing(tmp_path, monkeypatch):
    """Fresh install: no state.db yet → synth seed to bootstrap UI."""
    monkeypatch.delenv("ALGOTRADER_SYNTH_SEED", raising=False)
    assert should_seed_synth(str(tmp_path / "missing.db")) is True


def test_returns_true_when_token_row_empty(tmp_path, monkeypatch):
    """DB exists, broker_token row empty → synth."""
    db = _fresh_db(tmp_path)
    monkeypatch.delenv("ALGOTRADER_SYNTH_SEED", raising=False)
    assert should_seed_synth(db) is True


def test_returns_false_when_token_row_filled(tmp_path, monkeypatch):
    """DB exists, broker_token row has value → real data mode."""
    db = _fresh_db(tmp_path, value="t.real_token_here")
    monkeypatch.delenv("ALGOTRADER_SYNTH_SEED", raising=False)
    assert should_seed_synth(db) is False


def test_returns_true_when_explicit_env_override(tmp_path, monkeypatch):
    """ALGOTRADER_SYNTH_SEED=1 forces synth even with real token set."""
    db = _fresh_db(tmp_path, value="t.real_token_here")
    monkeypatch.setenv("ALGOTRADER_SYNTH_SEED", "1")
    assert should_seed_synth(db) is True


def test_returns_false_when_env_blank_and_token_present(tmp_path, monkeypatch):
    """Empty env var (not '1') doesn't trigger override."""
    db = _fresh_db(tmp_path, value="t.real_token_here")
    monkeypatch.setenv("ALGOTRADER_SYNTH_SEED", "")
    assert should_seed_synth(db) is False


def test_returns_true_when_db_unreadable(tmp_path, monkeypatch):
    """Corrupt DB → exception swallowed → synth fallback."""
    db = tmp_path / "broken.db"
    db.write_text("not a sqlite db")
    monkeypatch.delenv("ALGOTRADER_SYNTH_SEED", raising=False)
    assert should_seed_synth(str(db)) is True


def test_uses_default_path_when_no_arg(tmp_path, monkeypatch):
    """No arg + ALGOTRADER_DATA_DIR/state.db → checks that path."""
    db = tmp_path / "state.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE secrets (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    con.execute("INSERT INTO secrets (key, value, updated_at) VALUES ('broker_token', 't.real', datetime('now'))")
    con.commit()
    con.close()
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ALGOTRADER_SYNTH_SEED", raising=False)
    assert should_seed_synth() is False
