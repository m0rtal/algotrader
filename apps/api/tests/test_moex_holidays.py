# apps/api/tests/test_moex_holidays.py
from algotrader_api.scripts_import.import_moex_holidays import import_moex_holidays


def test_import_creates_holidays_table(tmp_path):
    db = tmp_path / "test.db"
    n = import_moex_holidays(str(db))
    assert n >= 70  # 2020..2027 inclusive
    import sqlite3

    con = sqlite3.connect(str(db))
    rows = con.execute("SELECT date, name FROM moex_holidays").fetchall()
    assert rows[0][0]  # first row has date
    assert rows[0][1]  # first row has name


def test_import_is_idempotent(tmp_path):
    db = tmp_path / "test.db"
    import_moex_holidays(str(db))
    first_count = _count(str(db))
    import_moex_holidays(str(db))
    second_count = _count(str(db))
    assert first_count == second_count


def test_cli_main_prints_import_count(tmp_path, capsys, monkeypatch):
    """Cover the `if __name__ == "__main__"` CLI entry point."""
    import runpy
    import sys
    from pathlib import Path

    db = tmp_path / "cli.db"
    pkg_root = Path(__import__("algotrader_api").__path__[0])
    script = pkg_root / "scripts_import" / "import_moex_holidays.py"
    monkeypatch.setattr(sys, "argv", [str(script), str(db)])
    runpy.run_path(str(script), run_name="__main__")
    out = capsys.readouterr().out.strip()
    assert out.startswith("Imported ")
    assert out.endswith(" MOEX holidays")
    assert _count(str(db)) >= 70


def _count(db_path):
    import sqlite3

    con = sqlite3.connect(db_path)
    return con.execute("SELECT COUNT(*) FROM moex_holidays").fetchone()[0]
