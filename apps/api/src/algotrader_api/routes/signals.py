"""Signals route.

GET /api/signals — returns the latest signals row-by-row from the
`signals` table. Today the schema (id, ticker, signal_date, score,
rank, created_at) does not carry the rich shape the UI table expects
(price, forecast5d, confidence, strength, regime, volume, updatedAt) —
to honor "no data = empty state" we return [] so the UI shows the
honest "Нет сигналов" rather than 404 noise from MSW passthrough.
When the ML pipeline starts writing extended signal rows, this becomes
a SELECT + mapper.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["signals"])


@router.get("/signals")
def get_signals() -> list[dict]:
    """Latest signals. Empty list until the pipeline populates extended rows."""
    return []
