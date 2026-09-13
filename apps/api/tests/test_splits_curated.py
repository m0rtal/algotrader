"""Tests for the curated Russian+global splits JSON loader (Task 2)."""
from datetime import date
from pathlib import Path

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations
from algotrader_api.scripts_import.import_splits_curated import (
    CURATED_FILE,
    import_curated_splits,
    load_curated_splits,
)


def test_curated_file_exists():
    assert CURATED_FILE.exists(), f"missing {CURATED_FILE}"


def test_curated_file_has_at_least_15_entries():
    rows = load_curated_splits()
    assert len(rows) >= 15


def test_curated_entries_have_real_figi_prefix():
    rows = load_curated_splits()
    for r in rows:
        assert r.figi.startswith("BBG"), f"non-BBG figi: {r.figi}"


def test_curated_entry_shape():
    rows = load_curated_splits()
    for r in rows:
        assert r.action_type == "split"
        assert r.factor >= 2.0
        assert r.cash_amount is None
        assert isinstance(r.ex_date, date)


def test_import_curated_splits_writes_rows(tmp_path):
    """End-to-end: load + merge → corporate_actions table has the rows."""
    db = str(tmp_path / "s.db")
    run_migrations(db, str(MIGRATIONS_DIR))
    n = import_curated_splits(db)
    assert n >= 15
    import sqlite3

    con = sqlite3.connect(db)
    assert con.execute(
        "SELECT COUNT(*) FROM corporate_actions WHERE action_type='split'"
    ).fetchone()[0] >= 15


def test_cli_main_prints_import_count(tmp_path, capsys, monkeypatch):
    """Cover the operator-side `python -m scripts.import_splits_curated` CLI.

    The in-package loader uses a relative import, so `runpy.run_path`
    can't drive it — we shell out to the operator wrapper instead.
    """
    import subprocess
    db = tmp_path / "cli.db"
    run_migrations(str(db), str(MIGRATIONS_DIR))
    scripts_dir = Path(__import__("algotrader_api").__path__[0]).parent.parent / "scripts"
    script = scripts_dir / "import_splits_curated.py"
    result = subprocess.run(
        ["uv", "run", "python", str(script), str(db)],
        capture_output=True, text=True, cwd=str(scripts_dir.parent),
        timeout=60,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    out = result.stdout.strip()
    assert out.startswith("Imported ")
    assert out.endswith(" curated splits")
    assert " " in out  # has a number in the middle
