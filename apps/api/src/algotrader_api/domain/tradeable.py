"""Single source of truth for tradeable asset classes.

Before this module existed, `TRADEABLE_CLASSES` lived in
`maintenance/cleanup.py` and the universe discovery loop in
`ingestion/universe.py` still fetched every class the broker
exposed (share/bond/etf/future/option). That meant cleanup had to
run *after* every backfill just to keep the instruments table
aligned with what the operator actually trades.

This module is the contract every layer agrees on:

- `ingestion/universe.py` filters rows to `TRADEABLE_CLASSES`
  before they ever reach `instruments`.
- `ingestion/backfill.py._list_instruments` filters on the same
  set at the SQL boundary, so a stale row from before this fix
  cannot sneak through.
- `maintenance/cleanup.py` reads `TRADEABLE_CLASSES` to compute
  the drop set, but the runtime no longer relies on it for
  correctness — only for emergency sweeps.

If you add a class (e.g. `currency`), update `TRADEABLE_CLASSES`
and the spec at `openspec/specs/data-fetch/spec.md` together.
"""
from __future__ import annotations

from typing import Iterable

# Set of `instruments.class` values the operator actively trades.
# Updates require a spec change in `data-fetch` and a corresponding
# test in `tests/test_tradeable.py`.
TRADEABLE_CLASSES: frozenset[str] = frozenset({"share", "etf", "bond"})


def is_tradeable(class_name: str | None) -> bool:
    """True iff `class_name` is in `TRADEABLE_CLASSES`.

    Null and empty strings are not tradeable — they represent
    metadata that never completed (e.g. an instrument that the
    broker returned without a `class` field).
    """
    if not class_name:
        return False
    return class_name in TRADEABLE_CLASSES


def filter_tradeable(rows: Iterable[dict]) -> list[dict]:
    """Drop non-tradeable rows. Stable: preserves input order."""
    return [r for r in rows if is_tradeable(r.get("class"))]
