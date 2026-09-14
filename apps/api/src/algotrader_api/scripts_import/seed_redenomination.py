"""Seed the 1998 ruble redenomination split row.

Idempotent: re-running is safe (INSERT OR IGNORE).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def seed_redenomination(db_path: str | Path) -> int:
    """Apply the 012 seed migration to ``db_path``.

    Returns the number of pre-1998 figis that received the split row.
    If no figis have bars pre-1998 in the DB (the common case for a
    fresh Tinkoff-sandbox-backed store), returns 0.
    """
    migration = (
        _ROOT
        / "algotrader_api"
        / "db"
        / "migrations"
        / "012_seed_1998_redenomination.sql"
    )
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(migration.read_text())
        conn.commit()
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM corporate_actions "
            "WHERE source LIKE '%redenomination%'"
        ).fetchone()
    finally:
        conn.close()
    return n


def main() -> int:  # pragma: no cover
    p = argparse.ArgumentParser(description="Seed 1998 redenomination split.")
    p.add_argument("db_path", type=Path)
    args = p.parse_args()

    n = seed_redenomination(args.db_path)
    print(f"Seeded 1998 redenomination split for {n} pre-1998 figis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
