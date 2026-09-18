"""Priority queue construction for backfill.

Orders tradable figis by gap size (descending) so rate-limit budget
is spent on the biggest missing-history gaps first.

Designed to be pure (no I/O except the DB read via
`compute_missing_dates`) and idempotent.
"""

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
    """Return `[(ticker, figi, gap_size), ...]` sorted by gap size DESC.

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
        A list of `(ticker, figi, gap_size)` tuples sorted by gap_size
        descending. Figis with gap_size == 0 are excluded.
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
    queue.sort(key=lambda x: -x[2])
    return queue
