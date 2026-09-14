"""Seed restricted_periods from migration 011.

Idempotent: re-running is safe (INSERT OR IGNORE).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def seed_restricted_periods(db_path: str | Path) -> int:
    """Apply the 011 seed migration to ``db_path``.

    Returns the number of rows that ended up in the restricted_periods
    table after the seed.
    """
    migration = (
        _ROOT
        / "algotrader_api"
        / "db"
        / "migrations"
        / "011_seed_restricted_periods.sql"
    )
    conn = sqlite3.connect(db_path)
    try:
        # The migration assumes the table already exists; create it
        # first so the seed is idempotent on fresh DBs too.
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS restricted_periods ("
            "  date   TEXT PRIMARY KEY,"
            "  reason TEXT NOT NULL,"
            "  source TEXT NOT NULL DEFAULT 'manual:moex_announcements'"
            ");"
        )
        conn.executescript(migration.read_text())
        conn.commit()
        (n,) = conn.execute("SELECT COUNT(*) FROM restricted_periods").fetchone()
    finally:
        conn.close()
    return n


def main() -> int:  # pragma: no cover
    p = argparse.ArgumentParser(description="Seed MOEX restricted periods.")
    p.add_argument("db_path", type=Path)
    args = p.parse_args()

    n = seed_restricted_periods(args.db_path)
    print(f"Seeded restricted_periods ({n} rows).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
