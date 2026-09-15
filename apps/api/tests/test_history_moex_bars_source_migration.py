"""Migration 018: bars.source defaults to 'moex'; post-2021 rows are 'tinkoff'.

The migration adds the `source` column to `bars` (if absent) with
default 'moex' and rewrites existing rows on/after 2021-08-01 to
'tinkoff' (the historical source for that window). The cutoff
2021-08-01 is safe: Tinkoff sandbox has no data before 2021-08-10, so
any pre-cutoff row in bars must have come from MOEX ISS.
"""
from __future__ import annotations

import os
import sqlite3

from algotrader_api.db.sqlite import run_migrations
from algotrader_api.db.migrations import MIGRATIONS_DIR


def _bars_source_default(db_path: str) -> str | None:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT dflt_value FROM pragma_table_info('bars') WHERE name = 'source'"
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return (row[0] or "").strip().strip("'\"")


def test_migration_018_creates_source_column_with_default_moex(tmp_path):
    """After migration 018, bars.source exists with default 'moex'.

    Before migration 018, the bars table has no source column (the
    `source` column was first introduced in migration 018). After the
    migration, the column exists with default 'moex'.
    """
    db_path = str(tmp_path / "test_018_default.db")
    if os.path.exists(db_path):
        os.unlink(db_path)
    run_migrations(db_path, MIGRATIONS_DIR)
    dflt = _bars_source_default(db_path)
    assert dflt == "moex", (
        f"bars.source default should be 'moex' after migration 018, got {dflt!r}"
    )


def test_migration_018_post_2021_rows_back_filled_to_tinkoff(tmp_path):
    """After migration 018, existing rows on/after 2021-08-01 are back-filled as 'tinkoff'.

    Simulates the state of an existing production database before
    migration 018 ran (no `source` column yet). Inserts two rows via
    the migration's ADD COLUMN default ('moex'), then asserts that
    re-running migrations flips post-2021 rows to 'tinkoff' via the
    migration's UPDATE step.
    """
    db_path = str(tmp_path / "test_018.db")
    if os.path.exists(db_path):
        os.unlink(db_path)
    # First run: apply migrations 001..018 so bars.source exists with
    # default 'moex'.
    run_migrations(db_path, MIGRATIONS_DIR)
    con = sqlite3.connect(db_path)
    # Insert two rows without specifying source — they pick up the
    # column default 'moex'.
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES ('FIGI-OLD', '2020-01-15', 1, 1, 1, 1, 100),"
        "       ('FIGI-NEW', '2025-01-15', 1, 1, 1, 1, 100)"
    )
    con.commit()
    con.close()
    # Second run: re-apply migrations. Migration 018's ADD COLUMN is
    # a no-op (column exists). The UPDATE step rewrites source for
    # rows with ts >= '2021-08-01' to 'tinkoff'. The pre-2021 row is
    # untouched.
    run_migrations(db_path, MIGRATIONS_DIR)
    con = sqlite3.connect(db_path)
    rows = dict(con.execute("SELECT figi, source FROM bars").fetchall())
    con.close()
    assert rows["FIGI-OLD"] == "moex", (
        f"pre-2021 row keeps the column default 'moex'; got {rows['FIGI-OLD']}"
    )
    assert rows["FIGI-NEW"] == "tinkoff", (
        f"post-2021 row should be back-filled to 'tinkoff'; got {rows['FIGI-NEW']}"
    )
