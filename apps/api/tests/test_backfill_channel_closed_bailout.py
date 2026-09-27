"""Regression: Tinkoff 'Channel is closed' must bail out, not retry-loop.

Backstory (2026-09-27 18:15 MSK):
  Production worker was hung in backfill_moex emitting
  `backfill.tinkoff.chunk.failed` 59 times in 5 minutes
  (gaps: 0.0001s — ~1kHz) on a dead gRPC channel. Zero
  bars were added. The retry decorator's non_rate_error
  path raises immediately, so the chunk loop was
  hammering the dead channel instead of bailing out.

Fix: in both ``_fetch_tinkoff_fallback_impl`` and the
main ``_backfill_one`` chunk loop, detect "Channel is closed"
and ``break`` out of the per-figi loop so the worker
cycle can return, the supervisor can SIGKILL+relaunch,
and ``RealTinkoffClient.__init__`` will build a fresh
channel on next cycle.

These tests pin the behaviour so a future refactor
doesn't reintroduce the runaway loop.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest


def _import_fallback():
    from algotrader_api.ingestion.backfill import _fetch_tinkoff_fallback_impl
    return _fetch_tinkoff_fallback_impl


def _import_retry_module():
    """AdaptiveRetry is needed by the fallback."""
    import algotrader_api.ingestion.retry as retry_mod
    return retry_mod


def _build_client_that_always_raises_channel_closed() -> MagicMock:
    """Mock client whose get_candles always raises
    'Channel is closed' regardless of args."""
    client = MagicMock()
    client.get_candles = AsyncMock(
        side_effect=Exception("Channel is closed."),
    )
    return client


@pytest.mark.asyncio
async def test_fetch_tinkoff_fallback_bails_on_channel_closed():
    """If the first chunk hits 'Channel is closed', the
    fallback must bail, NOT retry on the next chunk with
    the same dead channel."""
    fallback = _import_fallback()
    retry_mod = _import_retry_module()

    client = _build_client_that_always_raises_channel_closed()
    figi = "BBG000BHVSG6"
    ticker = "XLV"
    from_d = date(2015, 1, 1)
    to_d = date(2015, 1, 14)  # 14 days = 2 chunks

    result = await fallback(
        client=client,
        retry_mod=retry_mod,
        figi=figi,
        ticker=ticker,
        from_d=from_d,
        to_d=to_d,
    )

    # First chunk raised — fallback returned early. No data
    # fetched, no second chunk attempted.
    assert client.get_candles.await_count == 1, (
        f"expected exactly 1 chunk attempt before bailing, "
        f"got {client.get_candles.await_count} — the channel-"
        f"closed runaway loop is back"
    )
    assert result == [], (
        f"expected empty result on channel-dead bailing, got {result}"
    )


@pytest.mark.asyncio
async def test_fetch_tinkoff_fallback_channel_closed_does_not_hang_loop():
    """A 30-day window would normally try 5 chunks (7-day
    spans). With the bug, it'd retry the dead channel on
    every chunk. With the fix, only chunk 1 is attempted."""
    fallback = _import_fallback()
    retry_mod = _import_retry_module()

    client = _build_client_that_always_raises_channel_closed()

    result = await fallback(
        client=client,
        retry_mod=retry_mod,
        figi="BBG000BHVSG6",
        ticker="XLV",
        from_d=date(2015, 1, 1),
        to_d=date(2015, 1, 30),
    )

    assert client.get_candles.await_count == 1, (
        f"expected to bail after 1 chunk attempt, "
        f"but tried {client.get_candles.await_count} chunks — "
        f"channel-closed runaway loop is back"
    )
    assert result == []


@pytest.mark.asyncio
async def test_fetch_tinkoff_fallback_normal_errors_dont_bail():
    """Sanity check: a non-channel error (e.g. 'ValueError')
    must NOT trigger the channel-dead bail-out. The figi
    must continue to subsequent chunks so partial coverage
    is possible."""
    fallback = _import_fallback()
    retry_mod = _import_retry_module()

    client = MagicMock()
    client.get_candles = AsyncMock(side_effect=ValueError("bad input"))

    result = await fallback(
        client=client,
        retry_mod=retry_mod,
        figi="BBG000BHVSG6",
        ticker="XLV",
        from_d=date(2015, 1, 1),
        to_d=date(2015, 1, 30),
    )

    # Normal errors: continue past the failed chunk, try the
    # next one. We expect >1 attempt.
    assert client.get_candles.await_count > 1, (
        f"expected fallback to continue past non-channel errors, "
        f"but it bailed after {client.get_candles.await_count} attempt(s)"
    )
    assert result == []
