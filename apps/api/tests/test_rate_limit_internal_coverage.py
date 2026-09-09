"""Coverage tests for RateLimiter public properties + wait_for_capacity."""

import pytest

from algotrader_api.ingestion.rate_limit import RateLimiter


def test_rate_limiter_rate_and_period_properties():
    rl = RateLimiter(rate=15, period=60.0)
    assert rl.rate == 15
    assert rl.period == 60.0


def test_rate_limiter_default_construction():
    rl = RateLimiter()
    # Defaults from DEFAULT_RATE / DEFAULT_PERIOD constants.
    assert rl.rate > 0
    assert rl.period > 0


def test_rate_limiter_wait_for_capacity_returns_positive():
    rl = RateLimiter(rate=15, period=60.0)
    wait = rl.wait_for_capacity("get_candles")
    assert wait == 60.0 / 15.0


def test_rate_limiter_bucket_caches_across_calls():
    rl = RateLimiter(rate=14, period=60.0)
    b1 = rl._bucket_for("get_candles")
    b2 = rl._bucket_for("get_candles")
    assert b1 is b2
