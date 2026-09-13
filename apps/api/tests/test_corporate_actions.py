# apps/api/tests/test_corporate_actions.py
import runpy
import subprocess
import sys
from pathlib import Path

from algotrader_api.db import sqlite as sqlitedb
from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import get_connection


def _migrated_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    sqlitedb.run_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def test_corporate_actions_table_exists_after_migration(tmp_path):
    """After run_migrations the corporate_actions table must be present."""
    db = _migrated_db(tmp_path)
    con = get_connection(db)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='corporate_actions'"
    ).fetchall()
    assert rows, "corporate_actions table missing"


def test_corporate_actions_schema_is_strict(tmp_path):
    """The table must have the documented columns with strict types."""
    db = _migrated_db(tmp_path)
    con = get_connection(db)
    info = con.execute("PRAGMA table_info(corporate_actions)").fetchall()
    cols = {r[1]: r[2] for r in info}
    assert cols["figi"] == "TEXT"
    assert cols["action_type"] == "TEXT"
    assert cols["ex_date"] == "DATE"
    assert cols["factor"] == "REAL"
    assert cols["cash_amount"] == "REAL"


def test_corporate_actions_primary_key_on_figi_type_date(tmp_path):
    db = _migrated_db(tmp_path)
    con = get_connection(db)
    # PRAGMA table_info columns: 0=cid, 1=name, 2=type, 3=notnull, 4=dflt, 5=pk
    pk_cols = [
        r[1]
        for r in con.execute("PRAGMA table_info(corporate_actions)").fetchall()
        if r[5] > 0
    ]
    assert pk_cols == ["figi", "action_type", "ex_date"]


# --- Task 2: import script tests ---------------------------------------------

from algotrader_api.scripts_import.import_corporate_actions import (  # noqa: E402
    ACTION_TYPES,
    import_corporate_actions,
)


def test_import_corporate_actions_is_idempotent(tmp_path):
    n1 = import_corporate_actions(str(tmp_path / "test.db"))
    n2 = import_corporate_actions(str(tmp_path / "test.db"))
    assert n1 == n2
    con = get_connection(str(tmp_path / "test.db"))
    total = con.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0]
    assert total == n2


def test_import_corporate_actions_writes_known_rows(tmp_path):
    n = import_corporate_actions(str(tmp_path / "test.db"))
    assert n >= 5  # bundle at least 5 demo events
    con = get_connection(str(tmp_path / "test.db"))
    row = con.execute(
        "SELECT factor, cash_amount FROM corporate_actions "
        "WHERE figi=? AND action_type='split' ORDER BY ex_date DESC LIMIT 1",
        ("FIGI-SBER",),
    ).fetchone()
    assert row is not None and row[0] == 2.0  # 2-for-1 split


def test_import_corporate_actions_action_types_match_data(tmp_path):
    """Every row's action_type must be one of the documented enum values."""
    import_corporate_actions(str(tmp_path / "test.db"))
    con = get_connection(str(tmp_path / "test.db"))
    types = {r[0] for r in con.execute(
        "SELECT DISTINCT action_type FROM corporate_actions"
    ).fetchall()}
    assert types.issubset(set(ACTION_TYPES))


def test_cli_main_prints_import_count(tmp_path, capsys, monkeypatch):
    """Cover the `if __name__ == "__main__"` CLI entry point on the
    in-package module (mirrors the holidays test pattern)."""
    db = tmp_path / "cli.db"
    pkg_root = Path(__import__("algotrader_api").__path__[0])
    script = pkg_root / "scripts_import" / "import_corporate_actions.py"
    monkeypatch.setattr(sys, "argv", [str(script), str(db)])
    runpy.run_path(str(script), run_name="__main__")
    out = capsys.readouterr().out.strip()
    assert out.startswith("Imported ")
    assert out.endswith(" corporate actions")


def test_import_corporate_actions_via_operator_wrapper(tmp_path):
    """The scripts/import_corporate_actions.py thin wrapper delegates
    to the in-package implementation and prints to stdout."""
    db = tmp_path / "state.db"
    r = subprocess.run(
        [sys.executable, "-m", "scripts.import_corporate_actions", str(db)],
        cwd="/home/hermes/algotrader/apps/api",
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "Imported" in r.stdout
    # Verify rows landed in the DB the wrapper created.
    con = get_connection(str(db))
    total = con.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0]
    assert total >= 5
