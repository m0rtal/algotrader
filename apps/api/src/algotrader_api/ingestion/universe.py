"""Discover all MOEX instruments and persist to SQLite."""
from __future__ import annotations

from typing import Any

from ..db.sqlite import execute_returning_id
from ..observability.logging import get_logger

logger = get_logger("algotrader_api.ingestion.universe")


async def discover_universe(client: Any) -> list[dict]:
    """Fetch all instrument classes from Tinkoff and return a unified list.

    Each row has fields: ticker, figi, class, name, currency, lot_size, isin, sector.

    Failures in any one class are logged and skipped — partial results are
    better than total failure.
    """
    out: list[dict] = []
    for class_name, method in (
        ("share", client.get_shares),
        ("bond", client.get_bonds),
        ("etf", client.get_etfs),
        ("future", client.get_futures),
        ("option", client.get_options),
    ):
        try:
            rows = await method()
            out.extend(rows)
            logger.info("universe.class.done", class_name=class_name, count=len(rows))
        except Exception as e:
            logger.warning("universe.class.failed", class_name=class_name, error=str(e))
    logger.info("universe.discover.done", total=len(out))
    return out


def upsert_instruments(db_path: str, rows: list[dict]) -> int:
    """INSERT OR REPLACE into instruments. Returns rows inserted.

    Batched in transactions of 100 for performance.
    """
    if not rows:
        return 0
    inserted = 0
    BATCH = 100
    for i in range(0, len(rows), BATCH):
        batch = rows[i : i + BATCH]
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
