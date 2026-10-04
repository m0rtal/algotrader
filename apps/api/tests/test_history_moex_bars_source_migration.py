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
from pathlib import Path

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
    """Migration 018 labels rows already present before it first runs."""
    db_path = str(tmp_path / "test_018.db")
    before_018 = tmp_path / "before_018"
    before_018.mkdir()
    for migration in Path(MIGRATIONS_DIR).glob("*.sql"):
        if migration.name < "018_":
            (before_018 / migration.name).write_text(migration.read_text())
    run_migrations(db_path, str(before_018))
    assert _bars_source_default(db_path) is None
    con = sqlite3.connect(db_path)
    # Seed the legacy schema before the one-time data migration.
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES ('FIGI-OLD', '2020-01-15', 1, 1, 1, 1, 100),"
        "       ('FIGI-NEW', '2025-01-15', 1, 1, 1, 1, 100)"
    )
    con.commit()
    con.close()
    # First application of 018 must classify the existing rows.
    # Hash-matched migrations are skipped on later startup, by design.
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
