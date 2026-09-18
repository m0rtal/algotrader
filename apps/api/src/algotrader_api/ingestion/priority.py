"""Priority queue construction for backfill.

Orders tradable figis in two tiers:
1. Figis with ≥1 moex bar in `bars` (proven MOEX yield). Within tier:
   sorted by gap size DESC.
2. Figis with 0 moex bars (sourced/delisted from MOEX, or chain
   hasn't fetched them yet). Within tier: sorted by gap size DESC.

Tier 1 figis are processed first so the chain spends its rate-limit
budget on figis where MOEX actually has data, instead of probing
sanctions-delisted bonds for 30 seconds and returning nothing.

Designed to be pure (no I/O except the DB read via
`compute_missing_dates`) and idempotent.
"""

import sqlite3
from datetime import date
from typing import Callable

from algotrader_api.ingestion.backfill import compute_missing_dates


def compute_priority_queue(
    db_path: str,
    universe: list[tuple[str, str]],
    listed_from_lookup: Callable[[str], date],
    yesterday: date,
    gap_threshold_for_moex: int = 100,
) -> list[tuple[str, str, int]]:
    """Return `[(ticker, figi, gap_size), ...]` sorted by tier + gap DESC.

    Args:
        db_path: Path to the SQLite state DB.
        universe: List of `(ticker, figi)` pairs to consider.
        listed_from_lookup: Callable that returns the listed_from date
            for a ticker. If a ticker is not in the lookup, falls back
            to `date(2014, 1, 1)` (Tinkoff floor for sanctions-delisted).
        yesterday: The last trading day to consider for coverage.
        gap_threshold_for_moex: Figis with gap > this threshold get
            included in the queue. Figis with gap ≤ threshold are
            excluded (Tinkoff-only fetch is faster for tiny gaps).

    Returns:
        A list of `(ticker, figi, gap_size)` tuples sorted by:
        - Tier 1 (moex-yielding) first, then tier 2 (no moex data).
        - Within each tier, sorted by gap_size DESC.
        - Figis with gap_size == 0 are excluded.
    """
    queue: list[tuple[str, str, int]] = []
    for ticker, figi in universe:
        listed_from = listed_from_lookup(ticker)
        if listed_from > yesterday:
            continue
        missing = compute_missing_dates(figi, listed_from, yesterday, db_path)
        gap_size = len(missing)
        if gap_size == 0:
            continue
        if gap_size <= gap_threshold_for_moex:
            # Skip MOEX walk for tiny gaps — Tinkoff is faster.
            # (Future enhancement: include in queue with a flag.)
            continue
        queue.append((ticker, figi, gap_size))

    # Load set of figis that have at least one moex bar (single query).
    # These are "proven MOEX-yielding" — chain has successfully fetched
    # their data before, so MOEX has it.
    moex_yielding: set[str] = set()
    con = sqlite3.connect(db_path)
    try:
        for (figi,) in con.execute(
            "SELECT DISTINCT figi FROM bars WHERE source = 'moex'"
        ):
            moex_yielding.add(figi)
    finally:
        con.close()

    # Tier sort: tier 1 (moex-yielding) first, then tier 2 (no moex).
    # Within each tier: gap size DESC.
    queue.sort(key=lambda x: (0 if x[1] in moex_yielding else 1, -x[2]))
    return queue
