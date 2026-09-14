"""Real universe sync step for the daily chain.

Replaces the no-op `_step_universe_sync` in worker.py. Calls
`universe.discover_universe(client)` and `universe.upsert_instruments`,
returning the count of inserted/updated instruments.
"""
from __future__ import annotations

from typing import Any, Protocol


class _ClientProtocol(Protocol):
    async def get_shares(self) -> list[dict]: ...
    async def get_etfs(self) -> list[dict]: ...
    async def get_bonds(self) -> list[dict]: ...


async def run_universe_sync(
    db_path: str, client: _ClientProtocol
) -> int:
    """Discover tradeable instruments via the broker client and
    upsert them into the `instruments` table. Returns the number of
    rows inserted/updated.
    """
    from algotrader_api.ingestion import universe

    # Only call tradeable-class methods (matches the tradeable filter
    # at the ingest boundary; see domain/tradeable.py).
    rows = await universe.discover_universe(client)
    return universe.upsert_instruments(db_path, rows)
