"""Test for RealTinkoffClient reuse across asyncio.run invocations.

Bug ADAPT-12 (live smoke 2026-09-25): when _step_gap_recovery uses TWO
separate `asyncio.run` calls (one for recover_gaps, one for
_fill_trailing) sharing the same RealTinkoffClient instance, the second
loop fails with "Event loop is closed" because RealTinkoffClient caches
its AsyncClient on the first event loop and the cached channel dies
when that loop closes.

This test verifies the fix: aclose() the client between event-loop
invocations, so the next asyncio.run() rebuilds the channel fresh.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

# No pytest-asyncio — pytest-asyncio runs in an event loop and
# asyncio.run() from inside that fails. Use plain sync test.

from algotrader_api.ingestion.real_client import RealTinkoffClient


def test_real_client_ensure_works_across_event_loops():
    """Two separate asyncio.run() calls sharing one RealTinkoffClient.

    Without the fix: the second call returns the cached services bound
    to the dead first loop → "Event loop is closed" on real gRPC.

    With the fix: aclose() between calls, so the second asyncio.run
    rebuilds AsyncClient on its own loop.
    """
    client = RealTinkoffClient.__new__(RealTinkoffClient)
    client._token = "stub_token"  # noqa: SLF001
    client._target = "stub:0"  # noqa: SLF001
    client._request_timeout = 30.0  # noqa: SLF001
    client._client = None  # noqa: SLF001
    client._services = None  # noqa: SLF001

    constructed_loops: list[int] = []

    class FakeAsyncClient:
        def __init__(self, token, target):
            try:
                constructed_loops.append(id(asyncio.get_running_loop()))
            except RuntimeError:
                pass

        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *args):
            pass

    fake_sdk = MagicMock()
    fake_sdk.AsyncClient = FakeAsyncClient
    client._sdk = fake_sdk  # noqa: SLF001

    async def use_ensure():
        return await client._ensure()  # noqa: SLF001

    # First loop: build the channel
    s1 = asyncio.run(use_ensure())
    assert s1 is not None
    assert len(constructed_loops) == 1

    # FIX (ADAPT-12): aclose between calls so the next asyncio.run()
    # rebuilds the gRPC channel on the new event loop. Without aclose,
    # RealTinkoffClient._client is bound to the dead first loop → every
    # trailing Tinkoff fetch fails with "Event loop is closed" (the
    # live-smoke regression that left bars stale for 3 days).
    asyncio.run(client.aclose())
    s2 = asyncio.run(use_ensure())
    assert s2 is not None

    # Key assertion: 2 AsyncClient constructions (one per event loop).
    # Without fix: only 1 (second run reused dead cached channel).
    assert len(constructed_loops) == 2, (
        f"Expected 2 AsyncClient constructions (one per event loop), "
        f"got {len(constructed_loops)}. Without the fix, the second "
        f"asyncio.run reuses the dead cached channel."
    )
