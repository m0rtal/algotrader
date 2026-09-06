"""Tests for rate limiter and adaptive retry."""
from __future__ import annotations

import asyncio
import time

import pytest

from algotrader_api.ingestion.rate_limit import RateLimiter
from algotrader_api.ingestion.retry import AdaptiveRetry, _is_rate_limit_error


@pytest.mark.asyncio
async def test_rate_limiter_allows_burst_up_to_limit():
    rl = RateLimiter(rate=14, period=60.0)
    start = time.monotonic()
    for _ in range(14):
        await rl.acquire("get_shares")
    elapsed = time.monotonic() - start
    assert elapsed < 0.1  # 14 acquires happen fast


@pytest.mark.asyncio
async def test_rate_limiter_blocks_15th_call():
    rl = RateLimiter(rate=14, period=60.0)
    for _ in range(14):
        await rl.acquire("get_shares")
    start = time.monotonic()
    await rl.acquire("get_shares")  # 15th — should wait ~4.3s
    elapsed = time.monotonic() - start
    assert elapsed >= 3.0  # some wait expected


@pytest.mark.asyncio
async def test_rate_limiter_separate_buckets_per_method():
    rl = RateLimiter(rate=14, period=60.0)
    for _ in range(14):
        await rl.acquire("get_shares")
    # Different method should still have full quota
    start = time.monotonic()
    await rl.acquire("get_bonds")
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_rate_limiter_records_success_and_error():
    rl = RateLimiter()
    rl.record_success("get_shares")
    rl.record_success("get_shares")
    rl.record_error("get_shares")
    assert rl.error_rate("get_shares") == pytest.approx(1 / 3)


def test_is_rate_limit_error_matches_patterns():
    assert _is_rate_limit_error(Exception("RESOURCE_EXHAUSTED"))
    assert _is_rate_limit_error(Exception("rate limit exceeded"))
    assert _is_rate_limit_error(Exception("HTTP 429"))
    assert not _is_rate_limit_error(Exception("network timeout"))
    assert not _is_rate_limit_error(ValueError("bad arg"))


@pytest.mark.asyncio
async def test_retry_succeeds_immediately_on_success():
    policy = AdaptiveRetry(max_attempts=3, initial_delay=0.01)
    calls = []

    async def fn():
        calls.append(1)
        return "ok"

    result = await policy.run(fn)
    assert result == "ok"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_raises_non_rate_limit_immediately():
    policy = AdaptiveRetry(max_attempts=3, initial_delay=0.01)
    calls = []

    async def fn():
        calls.append(1)
        raise ValueError("bad arg")

    with pytest.raises(ValueError):
        await policy.run(fn)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_retries_on_rate_limit():
    policy = AdaptiveRetry(max_attempts=3, initial_delay=0.01, backoff_factor=2.0)
    calls = []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise Exception("RESOURCE_EXHAUSTED")
        return "ok"

    result = await policy.run(fn)
    assert result == "ok"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_retry_gives_up_after_max_attempts():
    policy = AdaptiveRetry(max_attempts=2, initial_delay=0.01)
    calls = []

    async def fn():
        calls.append(1)
        raise Exception("RESOURCE_EXHAUSTED")

    with pytest.raises(Exception, match="RESOURCE_EXHAUSTED"):
        await policy.run(fn)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_retry_backoff_grows_exponentially():
    policy = AdaptiveRetry(max_attempts=3, initial_delay=0.01, backoff_factor=2.0)
    delays = []

    async def fn():
        delays.append(time.monotonic())
        raise Exception("RESOURCE_EXHAUSTED")

    start = time.monotonic()
    with pytest.raises(Exception):
        await policy.run(fn)
    # 3 attempts with delays ~0.01, ~0.02 between them
    assert delays[1] - delays[0] >= 0.005
    assert delays[2] - delays[1] >= 0.01
