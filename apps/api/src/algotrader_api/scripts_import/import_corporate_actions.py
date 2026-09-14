# apps/api/src/algotrader_api/scripts_import/import_corporate_actions.py
"""Load static corporate actions JSON into the corporate_actions table.

Idempotent: re-running does not duplicate rows (PRIMARY KEY + INSERT OR REPLACE).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "corporate_actions.json"
ACTION_TYPES = ("split", "dividend")


def import_corporate_actions(db_path: str) -> int:
    """Import the bundled corporate_actions.json into the given SQLite DB.

    Returns the number of rows written (== len(rows) on first and every
    subsequent run, because INSERT OR REPLACE on the primary key).
    """
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS corporate_actions (
            figi        TEXT    NOT NULL,
            action_type TEXT    NOT NULL CHECK (action_type IN ('split','dividend')),
            ex_date     DATE    NOT NULL,
            factor      REAL    NOT NULL,
            cash_amount REAL,
            note        TEXT,
            PRIMARY KEY (figi, action_type, ex_date)
        );
    """)
    rows = json.loads(DATA_FILE.read_text())
    cur.executemany(
        "INSERT OR REPLACE INTO corporate_actions"
        "(figi, action_type, ex_date, factor, cash_amount, note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                r["figi"],
                r["action_type"],
                r["ex_date"],
                r["factor"],
                r.get("cash_amount"),
                r.get("note"),
            )
            for r in rows
        ],
    )
    con.commit()
    con.close()
    return len(rows)


if __name__ == "__main__":
    import sys

    n = import_corporate_actions(sys.argv[1])
    print(f"Imported {n} corporate actions")
