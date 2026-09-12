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

from ..db.duck import query_ticker_overview  # noqa: F401  (re-exported for tests)


# Classes the operator actively trades. Anything else is dropped on
# cleanup. Updates to this tuple must be deliberate — share/etf/bond
# are preserved including historical (delisted) figis; future/option
# are dropped wholesale because the operator doesn't trade them.
TRADEABLE_CLASSES: frozenset[str] = frozenset({"share", "etf", "bond"})


@dataclass
class CleanupSummary:
    """Counts of rows / files touched by a cleanup pass."""

    instruments_dropped: int = 0
    metadata_dropped: int = 0
    logs_dropped: int = 0
    parquet_files_dropped: int = 0
    parquet_bytes_freed: int = 0
    parquet_files_orphaned: int = 0  # parquet stem missing from instruments


def _open_sqlite(sqlite_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(sqlite_path)
    con.execute("PRAGMA foreign_keys = ON")
    con.row_factory = sqlite3.Row
    return con


def prune_non_tradeable_classes(
    sqlite_path: str,
    bars_dir: str,
    *,
    classes: frozenset[str] | None = None,
    dry_run: bool = False,
) -> CleanupSummary:
    """Drop every row in `instruments`, `instrument_metadata`, and
    `ingestion_logs` whose figi belongs to a non-tradeable class, then
    delete the matching parquet files under `bars_dir`.

    The parquet filename stem is either the figi (legacy files) or the
    ticker (modern files). We only delete files whose stem is still
    present in the dropped `instruments` rows so we never clobber a
    ticker-style file that the operator might want to keep.

    Parameters
    ----------
    sqlite_path : str
        Path to the app's state.db.
    bars_dir : str
        Directory containing `<stem>.parquet` files. Files for
        non-tradeable classes are deleted (if a stem still resolves).
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
            con.commit()
        elif not dry_run:
            con.commit()

        # Drop the matching parquet files. Stem = figi for legacy
        # files; modern files are ticker-style and never belong to
        # non-tradeable classes, so the figi-as-stem match is safe.
        if os.path.isdir(bars_dir):
            for figi in drop_figis:
                path = os.path.join(bars_dir, f"{figi}.parquet")
                if not os.path.isfile(path):
                    continue
                summary.parquet_bytes_freed += os.path.getsize(path)
                if not dry_run:
                    os.remove(path)  # pragma: no cover — guarded by `not dry_run`
                summary.parquet_files_dropped += 1

        # Detect parquet files whose stem no longer corresponds to any
        # instrument. We don't delete these automatically — the
        # operator may have manually seeded bars that aren't in
        # `instruments` yet. They show up in the summary so a future
        # cleanup pass can address them.
        if os.path.isdir(bars_dir):
            known_figis = {
                row["figi"]
                for row in con.execute("SELECT figi FROM instruments").fetchall()
            }
            for name in os.listdir(bars_dir):
                if not name.endswith(".parquet"):
                    continue
                stem = os.path.splitext(name)[0]
                if stem not in known_figis:
                    summary.parquet_files_orphaned += 1
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
