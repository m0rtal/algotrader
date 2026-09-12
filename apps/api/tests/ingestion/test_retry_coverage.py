"""Coverage tests for retry.py: success after failure + backoff reset."""

import asyncio
import time

import pytest

from algotrader_api.ingestion.retry import AdaptiveRetry as RetryPolicy


@pytest.mark.asyncio
async def test_run_returns_value_on_first_success():
    async def good():
        return 42
    rp = RetryPolicy(max_attempts=3)
    out = await rp.run(good)
    assert out == 42


@pytest.mark.asyncio
async def test_run_retries_rate_limit_then_succeeds():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("RESOURCE_EXHAUSTED: rate limit hit")
        return "ok"
    rp = RetryPolicy(max_attempts=3, initial_delay=0.01, backoff_factor=1.0)
    out = await rp.run(flaky)
    assert out == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_run_raises_immediately_for_non_rate_limit_error():
    """Non-rate-limit errors do NOT retry — they bubble up at attempt 1."""
    calls = {"n": 0}

    async def broken():
        calls["n"] += 1
        raise ValueError("bad input")
    rp = RetryPolicy(max_attempts=3, initial_delay=0.001)
    with pytest.raises(ValueError, match="bad input"):
        await rp.run(broken)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_run_raises_after_exhausting_attempts():
    async def always_fails():
        raise RuntimeError("nope")
    rp = RetryPolicy(max_attempts=2, initial_delay=0.001, backoff_factor=1.0)
    with pytest.raises(RuntimeError, match="nope"):
        await rp.run(always_fails)


@pytest.mark.asyncio
async def test_run_resets_backoff_after_long_success_streak(monkeypatch):
    """After a long enough streak of successes, last_failure_at resets."""
    # Force _state.last_failure_at to a known nonzero value first.
    rp = RetryPolicy(max_attempts=3, initial_delay=0.001, reset_after=0.05)
    rp._state.last_failure_at = 12345.0
    rp._state.consecutive_success_started = time.time() - 10.0  # long ago

    async def good():
        return "ok"

    out = await rp.run(good)
    assert out == "ok"
    # After run, _maybe_reset_backoff is called repeatedly inside the
    # loop. With consecutive_success_started set in the past and
    # reset_after=0.05, the reset branch fires.
    assert rp._state.last_failure_at == 0.0


def test_max_attempts_and_last_failure_at_properties():
    rp = RetryPolicy(max_attempts=7)
    assert rp.max_attempts == 7
    assert rp.last_failure_at == 0.0


@pytest.mark.asyncio
async def test_run_honours_server_ratelimit_reset():
    """When the SDK raises RESOURCE_EXHAUSTED with `ratelimit_reset=N`
    in the trailing metadata, AdaptiveRetry should sleep for at least N
    seconds before the next attempt. This avoids spamming retries
    against a window that the server has explicitly told us is closed.
    """
    from unittest.mock import patch

    sleep_durations: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_durations.append(seconds)

    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        # First two attempts: server-suggested reset = 5 seconds.
        # Third attempt: succeeds.
        if calls["n"] < 3:
            raise RuntimeError(
                "RESOURCE_EXHAUSTED: rate limit hit. "
                "Metadata(ratelimit_limit='600, 600;w=60', "
                "ratelimit_remaining=0, ratelimit_reset=5)"
            )
        return "ok"

    with patch("algotrader_api.ingestion.retry.asyncio.sleep", fake_sleep):
        rp = RetryPolicy(max_attempts=4, initial_delay=0.01, backoff_factor=2.0)
        out = await rp.run(flaky)

    assert out == "ok"
    assert calls["n"] == 3
    # Two backoffs recorded. Each should be >= the server-suggested 5s,
    # not the local exponential (0.01 → 0.02).
    assert sleep_durations == [5.0, 5.0]


@pytest.mark.asyncio
async def test_extract_retry_after_handles_grpc_trailing_metadata():
    """A live grpc.RpcError with trailing_metadata() should be parsed."""
    from algotrader_api.ingestion.retry import _extract_retry_after

    class FakeRpcError(Exception):
        def trailing_metadata(self):
            return [("ratelimit_limit", "600"), ("ratelimit_reset", "7")]

    out = _extract_retry_after(FakeRpcError())  # type: ignore[arg-type]
    assert out == 7.0


@pytest.mark.asyncio
async def test_extract_retry_after_returns_none_when_absent():
    from algotrader_api.ingestion.retry import _extract_retry_after

    assert _extract_retry_after(RuntimeError("network blip")) is None


@pytest.mark.asyncio
async def test_extract_retry_after_floors_tiny_values_to_one():
    """A reset value of 0 should still sleep at least 1s to avoid hot loop."""
    from algotrader_api.ingestion.retry import _extract_retry_after

    exc = RuntimeError("RESOURCE_EXHAUSTED ratelimit_reset=0")
    assert _extract_retry_after(exc) == 1.0
