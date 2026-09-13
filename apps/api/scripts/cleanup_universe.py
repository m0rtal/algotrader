#!/usr/bin/env python3
"""Operator-facing cleanup script.

Two phases, run in order:

  1. Reconcile `instruments` with a fresh broker snapshot — adds any
     shares/etfs/bonds the broker lists today that aren't in the DB.
     Existing rows are preserved (historical figis that have since
     been delisted stay in the table so their bars are reachable).
  2. Prune non-tradeable classes — drop everything in `instruments`,
     `instrument_metadata`, `ingestion_logs` and the matching
     parquet files for classes outside the tradeable set
     (`share`, `etf`, `bond`).

Re-running is idempotent.

Usage::

    python -m scripts.cleanup_universe             # actually do it
    python -m scripts.cleanup_universe --dry-run   # preview only
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Allow running as `python scripts/cleanup_universe.py` from repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from algotrader_api.config import get_settings  # noqa: E402
from algotrader_api.ingestion.client import make_client  # noqa: E402
from algotrader_api.maintenance.cleanup import (  # noqa: E402
    CleanupSummary,
    prune_non_tradeable_classes,
    reconcile_with_broker,
)


async def _broker_snapshot(client) -> dict[str, dict[str, str]]:
    """Map of figi → (ticker, name) for shares, etfs, bonds."""
    out: dict[str, dict[str, str]] = {"share": {}, "etf": {}, "bond": {}}
    for cls, fetcher in (
        ("share", client.get_shares),
        ("etf", client.get_etfs),
        ("bond", client.get_bonds),
    ):
        rows = await fetcher()
        for row in rows:
            figi = row.get("figi")
            if not figi:
                continue
            out[cls][figi] = (row.get("ticker") or "", row.get("name") or "")
    return out


def _format_summary(stage: str, summary: CleanupSummary) -> str:
    parts = [
        f"[{stage}] instruments_dropped={summary.instruments_dropped}",
        f"metadata_dropped={summary.metadata_dropped}",
        f"logs_dropped={summary.logs_dropped}",
        f"bars_dropped={summary.bars_dropped}",
        f"bars_bytes_freed={summary.bars_bytes_freed}",
    ]
    return " ".join(parts)


async def main_async(dry_run: bool) -> int:
    settings = get_settings()
    sqlite_path = settings.sqlite_path

    client = make_client(sqlite_path=sqlite_path)
    snapshot = await _broker_snapshot(client)
    share_figis = set(snapshot["share"].keys())
    etf_figis = set(snapshot["etf"].keys())
    bond_figis = set(snapshot["bond"].keys())
    print(
        f"broker snapshot: shares={len(share_figis)} etfs={len(etf_figis)} bonds={len(bond_figis)}"
    )

    reconcile_summary = reconcile_with_broker(
        sqlite_path,
        share_figis=share_figis,
        etf_figis=etf_figis,
        bond_figis=bond_figis,
        share_meta=snapshot["share"],
        etf_meta=snapshot["etf"],
        bond_meta=snapshot["bond"],
        dry_run=dry_run,
    )
    print(_format_summary("reconcile", reconcile_summary))

    prune_summary = prune_non_tradeable_classes(
        sqlite_path, dry_run=dry_run
    )
    print(_format_summary("prune", prune_summary))

    if dry_run:
        print("\n*** dry-run; nothing was changed ***")
    else:
        print("\n*** cleanup complete; restart the backend to pick up the new universe ***")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the summary but make no changes.",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
