"""Common corporate-actions infrastructure: dataclass + DB writer.

Used by the historical-splits derivation importer. Intentionally minimal
— no curated data, no JSON bootstrapping, no Tinkoff/MOEX importers.

If you need more (dividends, splits from MOEX ISS), see
``scripts_import/import_corporate_actions.py`` for the original full
writer (deprecated for split detection).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class CorporateActionRow:
    """A single corporate-action row, ready to be inserted."""

    figi: str
    action_type: str           # 'split' | 'dividend' | ...
    ex_date: object            # datetime.date or 'YYYY-MM-DD' str
    factor: float              # split factor (1.0 for dividends)
    cash_amount: float         # dividend amount in currency (0.0 for splits)
    note: str = ""
    source: str = ""           # provenance tag — MUST be non-empty for derived rows


def merge_into_corporate_actions(
    db_path: str, rows: list[CorporateActionRow]
) -> int:
    """Insert rows, replacing any existing row with the same
    (figi, action_type, ex_date) PK. Returns number of rows actually
    written."""
    if not rows:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        written = 0
        for r in rows:
            ex_date = r.ex_date.isoformat() if hasattr(r.ex_date, "isoformat") else str(r.ex_date)
            cur = conn.execute(
                "SELECT 1 FROM corporate_actions "
                "WHERE figi=? AND action_type=? AND ex_date=?",
                (r.figi, r.action_type, ex_date),
            )
            if cur.fetchone() is not None:
                continue  # idempotent — skip duplicates
            conn.execute(
                "INSERT INTO corporate_actions "
                "(figi, action_type, ex_date, factor, cash_amount, note, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    r.figi,
                    r.action_type,
                    ex_date,
                    r.factor,
                    r.cash_amount,
                    r.note,
                    r.source,
                ),
            )
            written += 1
        conn.commit()
        return written
    finally:
        conn.close()
