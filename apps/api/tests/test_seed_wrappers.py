"""Smoke tests for the seed wrappers.

The wrappers live at `apps/api/src/algotrader_api/scripts_import/`.
Calling them via `subprocess` + `python -m` requires the project's
venv on PATH and PYTHONPATH magic; the in-process tests are simpler
and exercise the same code.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from algotrader_api.scripts_import.seed_redenomination import (
    seed_redenomination,
)
from algotrader_api.scripts_import.seed_restricted_periods import (
    seed_restricted_periods,
)


def _init_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE instruments (figi TEXT PRIMARY KEY, ticker TEXT, class TEXT);"
        "CREATE TABLE bars (figi TEXT, ts TEXT, open REAL, high REAL,"
        "  low REAL, close REAL, volume INTEGER);"
        "CREATE TABLE corporate_actions ("
        "  figi TEXT NOT NULL, action_type TEXT NOT NULL, ex_date TEXT NOT NULL,"
        "  factor REAL NOT NULL, cash_amount REAL, note TEXT, source TEXT,"
        "  PRIMARY KEY (figi, action_type, ex_date));"
    )
    conn.commit()
    conn.close()


def test_seed_restricted_periods_wrapper_inserts_25_rows(tmp_path: Path):
    db = tmp_path / "test.db"
    _init_db(db)
    n = seed_restricted_periods(db)
    assert n == 25
    # Idempotent — second run inserts no new rows.
    n2 = seed_restricted_periods(db)
    assert n2 == 25


def test_seed_redenomination_wrapper_is_noop_without_pre_1998_data(
    tmp_path: Path,
):
    """If no figis have bars pre-1998 (the sandbox case), the seed
    inserts 0 rows — the wrapper still runs cleanly."""
    db = tmp_path / "test.db"
    _init_db(db)
    n = seed_redenomination(db)
    assert n == 0
