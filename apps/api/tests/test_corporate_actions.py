# apps/api/tests/test_corporate_actions.py
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
