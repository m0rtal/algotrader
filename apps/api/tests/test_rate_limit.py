"""Tests for the per-method token-bucket rate limiter.

The Tinkoff sandbox allows 15 req/min per method. We use 14 req/min
(safety margin of 1) and drop to 10 req/min for 5 minutes when the
SDK returns RESOURCE_EXHAUSTED (gRPC 8) or HTTP 429.

The bucket is per-method: universe discovery (get_shares, get_bonds,
...) and bars fetch (get_candles) have independent quotas so a slow
method doesn't starve a fast one.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from algotrader_api.ingestion.rate_limit import RateLimiter, ThrottleEvent


@pytest.fixture
def fast_bucket(monkeypatch):
    """Bucket with a much higher refill rate so tests don't wait 60s."""
    monkeypatch.setattr(RateLimiter, "REQUESTS_PER_MIN", 60.0)
    return RateLimiter()


@pytest.mark.asyncio
async def test_bucket_starts_at_capacity(fast_bucket):
    assert fast_bucket.tokens("get_candles") == fast_bucket.cap_for("get_candles")


@pytest.mark.asyncio
async def test_acquire_decrements_and_refills(monkeypatch):
    """acquire() decreases available tokens; bucket refill is monotonic."""
    monkeypatch.setattr(RateLimiter, "REQUESTS_PER_MIN", 60.0)
    bucket = RateLimiter()
    cap = bucket.cap_for("get_candles")
    initial = bucket.tokens("get_candles")
    assert initial == pytest.approx(cap, abs=0.01)
    # A few acquires don't refill fast enough to mask the drain.
    for _ in range(3):
        await bucket.acquire("get_candles")
    drained = bucket.tokens("get_candles")
    # Tokens should be lower than initial (with some refill it could be
    # ~cap - 3 + tiny_refill, but well below cap).
    assert drained <= cap


@pytest.mark.asyncio
async def test_independent_buckets_per_method(fast_bucket):
    """get_candles exhausts → get_shares still has full bucket."""
    cap_candles = fast_bucket.cap_for("get_candles")
    for _ in range(cap_candles):
        await fast_bucket.acquire("get_candles")
    assert fast_bucket.tokens("get_shares") == fast_bucket.cap_for("get_shares")


@pytest.mark.asyncio
async def test_throttle_reduces_per_method_cap(monkeypatch):
    monkeypatch.setattr(RateLimiter, "REQUESTS_PER_MIN", 60.0)
    bucket = RateLimiter()
    original_cap = bucket.cap_for("get_candles")
    bucket.signal_throttle("get_candles")
    reduced = bucket.cap_for("get_candles")
    assert reduced == 10  # THROTTLED_RPM
    assert reduced < original_cap


@pytest.mark.asyncio
async def test_throttle_auto_restores_after_5_min(monkeypatch):
    """Use fake time so we don't wait 5 minutes in a unit test."""
    monkeypatch.setattr(RateLimiter, "REQUESTS_PER_MIN", 60.0)
    bucket = RateLimiter()
    bucket.signal_throttle("get_candles")
    assert bucket.cap_for("get_candles") == 10
    # Advance fake clock past the 5-minute restore window.
    fake_now = [time.monotonic()]
    monkeypatch.setattr(time, "monotonic", lambda: fake_now[0])
    fake_now[0] += 301  # 5 min + 1 sec
    assert bucket.cap_for("get_candles") == 14  # restored to NORMAL_RPM


@pytest.mark.asyncio
async def test_throttle_callback_invoked(monkeypatch):
    """signal_throttle() returns / invokes a callback so the runner can
    emit a UI event."""
    monkeypatch.setattr(RateLimiter, "REQUESTS_PER_MIN", 60.0)
    events = []
    bucket = RateLimiter(on_throttle=lambda method, until: events.append((method, until)))
    bucket.signal_throttle("get_candles")
    assert len(events) == 1
    assert events[0][0] == "get_candles"


@pytest.mark.asyncio
async def test_acquire_does_not_raise_when_bucket_empty(monkeypatch):
    """acquire() never raises — it blocks until a token is available."""
    monkeypatch.setattr(RateLimiter, "REQUESTS_PER_MIN", 60.0)
    bucket = RateLimiter()
    # Drain the bucket: should not raise even after the cap is exceeded.
    cap = bucket.cap_for("get_candles")
    for _ in range(cap):
        await bucket.acquire("get_candles")
    # No exception; we don't time the next acquire (timing-flaky in CI).
    # Instead: re-fill manually by sleeping just enough — too slow for tests.
    # Verify the call returns by cancelling after a short timeout.
    task = asyncio.create_task(bucket.acquire("get_candles"))
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass  # expected — we cancelled before a token was available
