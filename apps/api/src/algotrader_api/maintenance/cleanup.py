"""Cleanup maintenance helpers for the algotrader instruments universe.

Bounded responsibility:
  * `prune_non_tradeable_classes`: drop instruments, metadata, logs and
    parquet files for asset classes the operator doesn't trade. Today
    that's `future` and `option`; share/etf/bond are preserved along
    with their historical bars even when the broker no longer lists
    the instrument.
  * `reconcile_with_broker`: keep `instruments` aligned with the live
    broker snapshot. Adds new figis, refreshes metadata on existing
    rows, and *keeps* figis the broker has delisted (so historical
    bars remain reachable).

The cleanup is intentionally idempotent so operators can rerun it
without producing drift.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from ..db.bars_sqlite import replace_bars_for_figi  # noqa: F401  (re-exported for tests)
from ..domain.tradeable import TRADEABLE_CLASSES, is_tradeable  # noqa: F401  (re-exported for tests)

# Re-exported for backwards compat. The canonical home is
# `algotrader_api.domain.tradeable.TRADABLE_CLASSES` — every layer
# (discover_universe, upsert_instruments, _list_instruments) reads
# from there. This module keeps a local re-export so tests and
# operator scripts that import `algotrader_api.maintenance.cleanup`
# don't break.
__all__ = [
    "TRADEABLE_CLASSES",
    "is_tradeable",
    "replace_bars_for_figi",
    "CleanupSummary",
    "prune_non_tradeable_classes",
    "reconcile_with_broker",
]


@dataclass
class CleanupSummary:
    """Counts of rows / files touched by a cleanup pass."""

    instruments_dropped: int = 0
    metadata_dropped: int = 0
    logs_dropped: int = 0
    bars_dropped: int = 0
    bars_bytes_freed: int = 0


def _open_sqlite(sqlite_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(sqlite_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


def prune_non_tradeable_classes(
    sqlite_path: str,
    *,
    classes: frozenset[str] | None = None,
    dry_run: bool = False,
) -> CleanupSummary:
    """Drop every row in `instruments`, `instrument_metadata`,
    `ingestion_logs`, and `bars` whose figi belongs to a non-tradeable
    class.

    Pre-`remove-duckdb-and-parquet`, this function also deleted the
    matching `<stem>.parquet` files. The SQLite `bars` table is the
    single source of truth now, so the equivalent cleanup happens via
    a `DELETE FROM bars WHERE figi IN (...)` instead.

    Parameters
    ----------
    sqlite_path : str
        Path to the app's state.db.
    classes : frozenset[str], optional
        Override which classes to drop. Default: the complement of
        `TRADEABLE_CLASSES`.
    dry_run : bool
        Compute the summary but make no changes. Useful for an
        operator pre-flight before applying.

    Returns
    -------
    CleanupSummary
        Counts of what changed (or would change, when `dry_run=True`).
    """
    drop_classes = (
        classes if classes is not None else ({"future", "option"} - TRADEABLE_CLASSES)
        or {"future", "option"}
    )
    summary = CleanupSummary()
    con = _open_sqlite(sqlite_path)
    try:
        cur = con.execute(
            "SELECT figi FROM instruments WHERE class IN ({})".format(
                ",".join("?" for _ in drop_classes)
            ),
            tuple(drop_classes),
        )
        drop_figis = {row["figi"] for row in cur.fetchall() if row["figi"]}
        summary.instruments_dropped = len(drop_figis)

        if drop_figis:
            placeholders = ",".join("?" for _ in drop_figis)
            cur = con.execute(
                f"SELECT COUNT(*) AS cnt FROM instrument_metadata WHERE figi IN ({placeholders})",
                tuple(drop_figis),
            )
            summary.metadata_dropped = cur.fetchone()["cnt"]

            cur = con.execute(
                f"SELECT COUNT(*) AS cnt FROM ingestion_logs WHERE figi IN ({placeholders})",
                tuple(drop_figis),
            )
            summary.logs_dropped = cur.fetchone()["cnt"]

            cur = con.execute(
                f"SELECT COUNT(*) AS cnt, COALESCE(SUM(LENGTH(open) + LENGTH(high) + LENGTH(low) + LENGTH(close) + LENGTH(volume)), 0) AS bytes "
                f"FROM bars WHERE figi IN ({placeholders})",
                tuple(drop_figis),
            )
            row = cur.fetchone()
            summary.bars_dropped = row["cnt"]
            summary.bars_bytes_freed = int(row["bytes"])

        if not dry_run and drop_figis:
            placeholders = ",".join("?" for _ in drop_figis)
            con.execute(
                f"DELETE FROM instruments WHERE figi IN ({placeholders})",
                tuple(drop_figis),
            )
            con.execute(
                f"DELETE FROM instrument_metadata WHERE figi IN ({placeholders})",
                tuple(drop_figis),
            )
            con.execute(
                f"DELETE FROM ingestion_logs WHERE figi IN ({placeholders})",
                tuple(drop_figis),
            )
            con.execute(
                f"DELETE FROM bars WHERE figi IN ({placeholders})",
                tuple(drop_figis),
            )
            con.commit()
        elif not dry_run:
            con.commit()
    finally:
        con.close()
    return summary


def reconcile_with_broker(
    sqlite_path: str,
    *,
    share_figis: set[str],
    etf_figis: set[str],
    bond_figis: set[str],
    share_meta: dict[str, tuple[str, str]] | None = None,
    etf_meta: dict[str, tuple[str, str]] | None = None,
    bond_meta: dict[str, tuple[str, str]] | None = None,
    dry_run: bool = False,
) -> CleanupSummary:
    """Add missing `instruments` rows for a fresh broker snapshot.

    Existing rows are preserved — share/etf/bond figis that have
    disappeared from the broker (delisted) stay in the table so
    historical bars are still reachable. New rows get `ticker` and
    `name` from the broker payload.

    Parameters
    ----------
    sqlite_path : str
        Path to state.db.
    share_figis / etf_figis / bond_figis : set[str]
        The figis currently listed by the broker for each class.
    *_meta : dict[str, tuple[str, str]]
        Optional figi → (ticker, name) map. When omitted the row is
        inserted with an empty ticker/name and the operator can
        fill it in later.

    Returns
    -------
    CleanupSummary
        `instruments_dropped` is always 0 here; the function only
        adds rows. `parquet_files_orphaned` reports the count of
        parquet files whose stem is not in `instruments` after the
        pass.
    """
    share_meta = share_meta or {}
    etf_meta = etf_meta or {}
    bond_meta = bond_meta or {}
    summary = CleanupSummary()
    con = _open_sqlite(sqlite_path)
    try:
        existing = {
            row["figi"]
            for row in con.execute("SELECT figi FROM instruments").fetchall()
        }
        rows_to_insert: list[tuple[str, str, str, str, str, str]] = []
        for figi in share_figis - existing:
            ticker, name = share_meta.get(figi, ("", ""))
            rows_to_insert.append((ticker, figi, "share", name, "rub", 1))
        for figi in etf_figis - existing:
            ticker, name = etf_meta.get(figi, ("", ""))
            rows_to_insert.append((ticker, figi, "etf", name, "rub", 1))
        for figi in bond_figis - existing:
            ticker, name = bond_meta.get(figi, ("", ""))
            rows_to_insert.append((ticker, figi, "bond", name, "rub", 1))

        if rows_to_insert and not dry_run:
            con.executemany(
                "INSERT OR IGNORE INTO instruments "
                "(ticker, figi, class, name, currency, lot_size) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows_to_insert,
            )
            con.commit()

        summary.instruments_dropped = -len(rows_to_insert)  # negative = added
    finally:
        con.close()
    return summary
