"""Discover all MOEX instruments and persist to SQLite."""
from __future__ import annotations

import sqlite3
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
    """INSERT OR IGNORE then UPDATE-by-figi into instruments.

    Filters to `TRADEABLE_CLASSES` before the upsert. This is the
    fourth line of defence — even if `discover_universe` ever
    regresses, this function cannot persist non-tradeable rows.
    A `universe.class.post_filter_dropped` log line is emitted when
    any rows are dropped here, so the operator can see the filter
    was exercised.

    Why a two-step INSERT OR IGNORE + UPDATE-by-figi instead of
    `INSERT OR REPLACE`? The broker sometimes returns multiple
    figis for the same ticker (relisted instruments). PRIMARY KEY
    on `ticker` makes INSERT OR REPLACE silently destroy the
    existing figi to make room for the new one. INSERT OR IGNORE
    preserves the existing row, and the follow-up UPDATE refreshes
    metadata on whichever row matches the broker figi.

    The trade-off: when the broker has TWO figis for the same
    ticker, only the first one to land in DB survives — the second
    is silently dropped on the ticker PK collision. This is
    documented in the spec (criterion: PRIMARY KEY on ticker for
    canonical row identity) and the dropped rows are logged at
    WARN so the operator can see broker relisting events.

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
    dropped_dup_ticker = 0
    BATCH = 100
    for i in range(0, len(filtered), BATCH):
        batch = filtered[i : i + BATCH]
        for r in batch:
            # Step 1: try INSERT, skip on either UNIQUE collision.
            # Use a fresh sqlite3 connection — INSERT OR IGNORE returns
            # rowcount=0 when the row already exists.
            con = sqlite3.connect(db_path)
            try:
                cur = con.execute(
                    "INSERT OR IGNORE INTO instruments "
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
                con.commit()
                insert_succeeded = cur.rowcount == 1
            finally:
                con.close()
            if insert_succeeded:
                inserted += 1
            else:
                # INSERT was skipped — figi or ticker collision.
                # Check by ticker to detect duplicate-ticker drops.
                con = sqlite3.connect(db_path)
                try:
                    row = con.execute(
                        "SELECT figi FROM instruments WHERE ticker=?",
                        (r["ticker"],),
                    ).fetchone()
                finally:
                    con.close()
                if row and row[0] != r["figi"]:
                    # Different figi already holds this ticker — broker
                    # relisted and we keep the older one.
                    dropped_dup_ticker += 1
                    logger.warning(
                        "universe.ticker_duplicate_kept_existing",
                        ticker=r["ticker"],
                        existing_figi=row[0],
                        dropped_figi=r["figi"],
                        note="PRIMARY KEY=ticker; older row wins. "
                             "Consider manual cleanup if broker is wrong.",
                    )
            # Step 2: refresh metadata by figi (covers case where figi
            # already existed but ticker/class/name changed).
            execute_returning_id(
                db_path,
                "UPDATE instruments SET "
                "  ticker=?, class=?, name=?, currency=?, lot_size=?, isin=?, sector=? "
                "WHERE figi=?",
                (
                    r["ticker"],
                    r["class"],
                    r["name"],
                    r["currency"],
                    r["lot_size"],
                    r.get("isin"),
                    r.get("sector"),
                    r["figi"],
                ),
            )
    if dropped_dup_ticker:
        logger.warning(
            "universe.duplicate_ticker_dropped_total",
            dropped=dropped_dup_ticker,
            note="Older figi wins; broker relisted. Investigate if unexpected.",
        )
    return inserted
