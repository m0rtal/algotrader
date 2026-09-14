"""Seed the 1998 ruble redenomination split row.

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
    p = argparse.ArgumentParser(description="Seed 1998 redenomination split.")
    p.add_argument("db_path", type=Path)
    args = p.parse_args()

    migration = (
        _ROOT / "src/algotrader_api/db/migrations/012_seed_1998_redenomination.sql"
    )
    conn = sqlite3.connect(args.db_path)
    try:
        conn.executescript(migration.read_text())
        conn.commit()
    finally:
        conn.close()

    print("Seeded 1998 redenomination split (1000:1) for pre-1998 figis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
