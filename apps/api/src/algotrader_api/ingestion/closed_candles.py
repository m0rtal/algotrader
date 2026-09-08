"""Single source of truth for "is this candle ready to write to parquet?"

Tinkoff's SDK marks each candle with `is_complete: bool` — `True` for
finalised end-of-day bars, `False` for the in-progress current-day bar
that streams with partial price. The backfill runner writes only closed
bars; historical analytics break otherwise (a partial snapshot
confuses drawdown calculations).

Why this helper exists: every writer needs to agree on the rule, and
a future SDK rename of `is_complete` should be a one-file fix, not
spread across `bars.py`, `backfill.py`, `incremental.py`, etc.
"""
from __future__ import annotations

from typing import Any


def is_closed_candle(candle: Any) -> bool:
    """Return True if the candle is safe to persist.

    Rules:
    - `candle.is_complete is True` → closed, keep it.
    - `candle.is_complete is False` → open (in-progress), drop it.
    - `candle.is_complete is None` → proto3 unset; treat as open
      (the server didn't confirm finalisation), drop it.
    - Attribute missing entirely → assume closed. This is a defensive
      fallback for older SDK versions or test mocks; the runner also
      filters by `candle.time < today_utc` as a safety net, so a wrong
      "closed" classification here only costs one row in parquet.
    """
    try:
        flag = candle.is_complete
    except AttributeError:
        return True
    if flag is None:
        return False
    return bool(flag)
