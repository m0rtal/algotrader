# apps/api/src/algotrader_api/scripts_import/import_moex_holidays.py
"""Load MOEX trading-day calendar into moex_holidays table.

Idempotent: safe to run multiple times.
"""
import json
import sqlite3
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "moex_holidays.json"


def import_moex_holidays(db_path: str) -> int:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS moex_holidays (
            date TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
    """)
    holidays = json.loads(DATA_FILE.read_text())
    cur.executemany(
        "INSERT OR REPLACE INTO moex_holidays(date, name) VALUES (?, ?)",
        [(h["date"], h["name"]) for h in holidays],
    )
    con.commit()
    con.close()
    return len(holidays)


if __name__ == "__main__":
    import sys

    n = import_moex_holidays(sys.argv[1])
    print(f"Imported {n} MOEX holidays")
