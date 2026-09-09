"""Coverage tests for admin.py private helpers and edge paths."""

import json
import sqlite3
from pathlib import Path

from algotrader_api.routes.admin import _resolve_target_from_settings


def _seed_settings(path: Path, value: dict | None) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, version TEXT)"
    )
    if value is not None:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("main", json.dumps(value)),
        )
    conn.commit()
    conn.close()


def test_resolve_target_no_settings_row(tmp_path):
    db = tmp_path / "state.db"
    _seed_settings(db, None)
    assert _resolve_target_from_settings(str(db)) is None


def test_resolve_target_with_broker_environment_sandbox(tmp_path):
    db = tmp_path / "state.db"
    _seed_settings(db, {"broker": {"environment": "sandbox"}})
    assert _resolve_target_from_settings(str(db)) == "sandbox"


def test_resolve_target_with_broker_environment_production(tmp_path):
    db = tmp_path / "state.db"
    _seed_settings(db, {"broker": {"environment": "production"}})
    assert _resolve_target_from_settings(str(db)) == "production"


def test_resolve_target_with_invalid_environment_value(tmp_path):
    db = tmp_path / "state.db"
    _seed_settings(db, {"broker": {"environment": "bogus"}})
    assert _resolve_target_from_settings(str(db)) is None


def test_resolve_target_without_broker_key(tmp_path):
    db = tmp_path / "state.db"
    _seed_settings(db, {"someOther": {"environment": "sandbox"}})
    assert _resolve_target_from_settings(str(db)) is None


def test_resolve_target_with_garbage_json_returns_none(tmp_path):
    db = tmp_path / "state.db"
    _seed_settings(db, {"broker": "this should be a dict"})
    # broker is not a dict → falls through to return None.
    assert _resolve_target_from_settings(str(db)) is None
