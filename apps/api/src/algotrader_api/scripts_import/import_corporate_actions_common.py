# apps/api/src/algotrader_api/scripts_import/import_corporate_actions_common.py
"""Shared writer + dataclass for corporate_actions importers.

Each importer (Tinkoff / MOEX ISS / curated JSON) builds a list of
CorporateActionRow and calls merge_into_corporate_actions(). The merge is
idempotent via INSERT OR REPLACE on the (figi, action_type, ex_date) PK.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

ALLOWED_ACTION_TYPES = ("split", "dividend")


class InvalidActionType(ValueError):
    """Raised when a CorporateActionRow has an unsupported action_type."""


@dataclass(frozen=True)
class CorporateActionRow:
    figi: str
    action_type: str  # 'split' | 'dividend'
    ex_date: date
    factor: float
    cash_amount: Optional[float]
    note: str = ""
    # `source` records the ingestion path that produced this row (e.g.
    # 'moex:iss:dividends', 'tinkoff:dividends', 'moex_iss_snapshots',
    # 'curated'). Defaults to '' for backward compatibility with callers
    # that don't set it; importers are expected to fill it in.
    source: str = ""

    def __post_init__(self):
        if self.action_type not in ALLOWED_ACTION_TYPES:
            raise InvalidActionType(
                f"{self.action_type!r} not in {ALLOWED_ACTION_TYPES}"
            )
        if self.factor <= 0:
            raise ValueError(f"factor must be > 0, got {self.factor}")


def merge_into_corporate_actions(
    db_path: str, rows: Iterable[CorporateActionRow],
) -> int:
    """Insert or replace each row. Returns the number of rows that were
    fed to the writer (idempotency means the DB row count after the call
    may not increase on repeat runs)."""
    rows = list(rows)
    if not rows:
        return 0
    con = sqlite3.connect(db_path)
    try:
        con.executemany(
            """
            INSERT OR REPLACE INTO corporate_actions
                (figi, action_type, ex_date, factor, cash_amount, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (r.figi, r.action_type, r.ex_date.isoformat(),
                 r.factor, r.cash_amount, r.note)
                for r in rows
            ],
        )
        con.commit()
    finally:
        con.close()
    return len(rows)
