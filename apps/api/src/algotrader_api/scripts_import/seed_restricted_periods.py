"""Seed restricted_periods from migration 011.

Idempotent: re-running is safe (INSERT OR IGNORE).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "src"))


def main() -> int:
    p = argparse.ArgumentParser(description="Seed MOEX restricted periods.")
    p.add_argument("db_path", type=Path)
    args = p.parse_args()

    migration = (
        _ROOT / "src/algotrader_api/db/migrations/011_seed_restricted_periods.sql"
    )
    conn = sqlite3.connect(args.db_path)
    try:
        conn.executescript(migration.read_text())
        conn.commit()
    finally:
        conn.close()

    print("Seeded restricted_periods (36 rows: 2022-02-24 → 2022-03-31).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
