# apps/api/src/algotrader_api/scripts_import/import_splits_curated.py
"""Load the bundled Russian+global splits curated JSON.

This is the bootstrap: Tinkoff SDK has no native splits feed, so we
ship a one-time curated list. After this initial import, ongoing split
detection should diff face_value/lot over time (separate feature).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .import_corporate_actions_common import (
    CorporateActionRow,
    merge_into_corporate_actions,
)

CURATED_FILE = Path(__file__).parent / "data" / "splits_curated.json"


def load_curated_splits() -> list[CorporateActionRow]:
    """Read the bundled JSON and return a list of CorporateActionRow."""
    raw = json.loads(CURATED_FILE.read_text())
    out: list[CorporateActionRow] = []
    for entry in raw:
        out.append(CorporateActionRow(
            figi=entry["figi"],
            action_type="split",
            ex_date=date.fromisoformat(entry["ex_date"]),
            factor=float(entry["factor"]),
            cash_amount=None,
            note=entry.get("note", ""),
        ))
    return out


def import_curated_splits(db_path: str) -> int:
    rows = load_curated_splits()
    return merge_into_corporate_actions(db_path, rows)
