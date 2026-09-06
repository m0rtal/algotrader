"""In-memory fake Tinkoff client for unit tests.

Stores responses per method and records all calls. Tests assert against
the recorded calls list (e.g. "GetShares was called with status=BASE").

Usage:
    client = InMemoryTinkoffClient()
    client.set_shares([{...}, {...}])
    result = await client.get_shares()
    assert result == [...]
    assert client.calls["get_shares"] == [()]
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Storage:
    accounts: list[dict] = field(default_factory=list)
    shares: list[dict] = field(default_factory=list)
    bonds: list[dict] = field(default_factory=list)
    etfs: list[dict] = field(default_factory=list)
    futures: list[dict] = field(default_factory=list)
    options: list[dict] = field(default_factory=list)
    candles_by_figi: dict[str, list[dict]] = field(default_factory=dict)
    calls: dict[str, list[tuple]] = field(default_factory=lambda: {
        "get_accounts": [], "get_shares": [], "get_bonds": [], "get_etfs": [],
        "get_futures": [], "get_options": [], "get_candles": [],
    })
    closed: bool = False
    # Per-method call counters for rate-limit assertions
    call_counts: dict[str, int] = field(default_factory=dict)


class InMemoryTinkoffClient:
    """Pretends to be tinkoff.invest.AsyncClient. Records every call.

    Thread-safe — stores responses under a lock so tests can preload data
    from a different thread than the one running the assertions.

    For get_candles: if the preloaded candles have dates outside the requested
    range, they are filtered to match. This mirrors how the real Tinkoff API
    ignores out-of-range dates.
    """

    def __init__(self) -> None:
        self._storage = _Storage()
        self._lock = threading.Lock()

    # ─── Test pre-loaders ────────────────────────────────────────────
    def set_shares(self, shares: list[dict]) -> None:
        with self._lock:
            self._storage.shares = list(shares)

    def set_bonds(self, bonds: list[dict]) -> None:
        with self._lock:
            self._storage.bonds = list(bonds)

    def set_etfs(self, etfs: list[dict]) -> None:
        with self._lock:
            self._storage.etfs = list(etfs)

    def set_futures(self, futures: list[dict]) -> None:
        with self._lock:
            self._storage.futures = list(futures)

    def set_options(self, options: list[dict]) -> None:
        with self._lock:
            self._storage.options = list(options)

    def set_candles(self, figi: str, candles: list[dict]) -> None:
        with self._lock:
            self._storage.candles_by_figi[figi] = list(candles)

    # ─── TinkoffClient interface ──────────────────────────────────────
    async def get_accounts(self) -> list[dict]:
        with self._lock:
            self._storage.calls["get_accounts"].append(())
            self._storage.call_counts["get_accounts"] = (
                self._storage.call_counts.get("get_accounts", 0) + 1
            )
            return list(self._storage.accounts)

    async def get_shares(self) -> list[dict]:
        with self._lock:
            self._storage.calls["get_shares"].append(())
            self._storage.call_counts["get_shares"] = (
                self._storage.call_counts.get("get_shares", 0) + 1
            )
            return list(self._storage.shares)

    async def get_bonds(self) -> list[dict]:
        with self._lock:
            self._storage.calls["get_bonds"].append(())
            self._storage.call_counts["get_bonds"] = (
                self._storage.call_counts.get("get_bonds", 0) + 1
            )
            return list(self._storage.bonds)

    async def get_etfs(self) -> list[dict]:
        with self._lock:
            self._storage.calls["get_etfs"].append(())
            self._storage.call_counts["get_etfs"] = (
                self._storage.call_counts.get("get_etfs", 0) + 1
            )
            return list(self._storage.etfs)

    async def get_futures(self) -> list[dict]:
        with self._lock:
            self._storage.calls["get_futures"].append(())
            self._storage.call_counts["get_futures"] = (
                self._storage.call_counts.get("get_futures", 0) + 1
            )
            return list(self._storage.futures)

    async def get_options(self) -> list[dict]:
        with self._lock:
            self._storage.calls["get_options"].append(())
            self._storage.call_counts["get_options"] = (
                self._storage.call_counts.get("get_options", 0) + 1
            )
            return list(self._storage.options)

    async def get_candles(
        self,
        *,
        figi: str,
        date_from: str,
        date_to: str,
        interval: str = "CANDLE_INTERVAL_DAY",
    ) -> list[dict]:
        with self._lock:
            self._storage.calls["get_candles"].append((figi, date_from, date_to, interval))
            self._storage.call_counts["get_candles"] = (
                self._storage.call_counts.get("get_candles", 0) + 1
            )
            all_candles = list(self._storage.candles_by_figi.get(figi, []))
        # Filter by date range to mirror real Tinkoff API behavior.
        # Candles with ts < date_from or ts > date_to are excluded.
        return [c for c in all_candles if date_from <= c["ts"] <= date_to]

    async def aclose(self) -> None:
        with self._lock:
            self._storage.closed = True

    # ─── Test assertions ─────────────────────────────────────────────
    def call_count(self, method: str) -> int:
        with self._lock:
            return self._storage.call_counts.get(method, 0)

    def calls_for(self, method: str) -> list[tuple]:
        with self._lock:
            return list(self._storage.calls.get(method, []))

    def is_closed(self) -> bool:
        with self._lock:
            return self._storage.closed
