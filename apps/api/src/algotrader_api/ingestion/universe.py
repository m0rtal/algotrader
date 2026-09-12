"""Discover all MOEX instruments and persist to SQLite."""
from __future__ import annotations

from typing import Any

from ..db.sqlite import execute_returning_id
from ..domain.tradeable import TRADEABLE_CLASSES, filter_tradeable
from ..observability.logging import get_logger

logger = get_logger("algotrader_api.ingestion.universe")


async def discover_universe(client: Any) -> list[dict]:
    """Fetch only the tradeable instrument classes from Tinkoff.

    Filters at the *source*: we only call `client.get_<class>()` for
    classes in `TRADEABLE_CLASSES`. Anything else (today: `future`,
    `option`) is never requested and never persisted. This is the
    first line of defence against the universe slowly drifting to
    tens of thousands of non-tradeable figis.

    Failures in any one class are logged and skipped — partial
    results are better than total failure.
    """
    methods_by_class = {
        "share": client.get_shares,
        "bond": client.get_bonds,
        "etf": client.get_etfs,
        "future": client.get_futures,
        "option": client.get_options,
    }
    out: list[dict] = []
    for class_name, method in methods_by_class.items():
        if class_name not in TRADEABLE_CLASSES:
            logger.info(
                "universe.class.skipped",
                class_name=class_name,
                reason="not in TRADEABLE_CLASSES",
            )
            continue
        try:
            rows = await method()
            out.extend(rows)
            logger.info("universe.class.done", class_name=class_name, count=len(rows))
        except Exception as e:
            logger.warning("universe.class.failed", class_name=class_name, error=str(e))
    # Belt and braces: if a stray non-tradeable class sneaks past the
    # broker SDK (e.g. a new class the SDK starts returning), filter
    # here before the upsert. This is the second line of defence.
    filtered = filter_tradeable(out)
    dropped = len(out) - len(filtered)
    if dropped:
        logger.warning(
            "universe.class.post_filter_dropped",
            dropped=dropped,
            total=len(out),
        )
    logger.info("universe.discover.done", total=len(filtered))
    return filtered


def upsert_instruments(db_path: str, rows: list[dict]) -> int:
    """INSERT OR REPLACE into instruments. Returns rows inserted.

    Filters to `TRADEABLE_CLASSES` before the upsert. This is the
    fourth line of defence — even if `discover_universe` ever
    regresses, this function cannot persist non-tradeable rows.
    A `universe.class.post_filter_dropped` log line is emitted when
    any rows are dropped here, so the operator can see the filter
    was exercised.

    Batched in transactions of 100 for performance.
    """
    from ..domain.tradeable import filter_tradeable

    if not rows:
        return 0
    filtered = filter_tradeable(rows)
    dropped = len(rows) - len(filtered)
    if dropped:
        logger.warning(
            "universe.class.post_filter_dropped",
            dropped=dropped,
            total=len(rows),
        )
    if not filtered:
        return 0
    inserted = 0
    BATCH = 100
    for i in range(0, len(filtered), BATCH):
        batch = filtered[i : i + BATCH]
        for r in batch:
            execute_returning_id(
                db_path,
                "INSERT OR REPLACE INTO instruments "
                "(ticker, figi, class, name, currency, lot_size, isin, sector) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["ticker"],
                    r["figi"],
                    r["class"],
                    r["name"],
                    r["currency"],
                    r["lot_size"],
                    r.get("isin"),
                    r.get("sector"),
                ),
            )
            inserted += 1
    return inserted
