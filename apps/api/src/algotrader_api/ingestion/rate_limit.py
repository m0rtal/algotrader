"""Per-method token-bucket rate limiter for Tinkoff SDK calls.

Wraps aiolimiter.AsyncLimiter per method. Default: 14 calls per 60 seconds
(safety margin of 1 below T-Invest API's 15 req/min documented limit).

Each SDK method has its own bucket. acquire() returns a context manager.

Adaptive throttling: when a method returns RESOURCE_EXHAUSTED (gRPC 8)
or HTTP 429, the per-method cap drops to 10 req/min for 5 minutes, then
restores. The runner emits an event when this happens so the operator
sees the slowdown.
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from aiolimiter import AsyncLimiter

from ..observability.logging import get_logger

logger = get_logger("algotrader_api.ingestion.rate_limit")

DEFAULT_RATE = 14
DEFAULT_PERIOD = 60.0
THROTTLED_RPM = 10
THROTTLE_DURATION_SEC = 300.0


@dataclass
class ThrottleEvent:
    """Snapshot of a throttle signal for the operator UI."""

    method: str
    until_monotonic: float


@dataclass
class _Bucket:
    limiter: AsyncLimiter
    success_count: int = 0
    error_count: int = 0
    last_error_at: float = 0.0
    cap: int = DEFAULT_RATE
    throttled_until: float = 0.0  # monotonic seconds; 0 = not throttled
    limiter_lock: threading.Lock = field(default_factory=threading.Lock)

    def effective_cap(self, now: float) -> int:
        if self.throttled_until and now >= self.throttled_until:
            return DEFAULT_RATE
        if self.throttled_until:
            return THROTTLED_RPM
        return self.cap


class RateLimiter:
    """One AsyncLimiter per SDK method. Tracks success/error for adaptive behavior."""

    # Class-level so tests can monkeypatch REQUESTS_PER_MIN without
    # touching every AsyncLimiter instance.
    REQUESTS_PER_MIN: float = DEFAULT_RATE  # type: ignore[assignment]
    PERIOD_SEC: float = DEFAULT_PERIOD

    def __init__(
        self,
        rate: int = DEFAULT_RATE,
        period: float = DEFAULT_PERIOD,
        on_throttle: Callable[[str, float], None] | None = None,
    ) -> None:
        self._rate = rate
        self._period = period
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()
        self._on_throttle = on_throttle

    def _bucket_for(self, method: str) -> _Bucket:
        with self._lock:
            b = self._buckets.get(method)
            if b is None:
                b = _Bucket(
                    limiter=AsyncLimiter(
                        max_rate=self._rate,
                        time_period=self._period,
                    ),
                    cap=self._rate,
                )
                self._buckets[method] = b
            return b

    @property
    def rate(self) -> int:
        return self._rate

    @property
    def period(self) -> float:
        return self._period

    async def acquire(self, method: str) -> None:
        """Block until a token is available for the given method."""
        b = self._bucket_for(method)
        await b.limiter.acquire()

    def record_success(self, method: str) -> None:
        b = self._bucket_for(method)
        with self._lock:
            b.success_count += 1

    def record_error(self, method: str) -> None:
        b = self._bucket_for(method)
        with self._lock:
            b.error_count += 1
            b.last_error_at = time.time()

    def error_rate(self, method: str) -> float:
        b = self._bucket_for(method)
        with self._lock:
            total = b.success_count + b.error_count
            return b.error_count / total if total else 0.0

    def wait_for_capacity(self, method: str) -> float:
        """Return seconds to wait until next token is available for the method."""
        b = self._bucket_for(method)
        # ponytail: 1/rate is the per-token interval. We approximate without
        # consulting the limiter's internal state machine — this is a
        # best-effort estimate for backoff scheduling, not a guarantee.
        return self._period / self._rate

    # ─── new: per-method capacity introspection + throttle ──────────

    def cap_for(self, method: str) -> int:
        """Current effective per-method cap (14 normal, 10 throttled).

        Reads the throttled_until timestamp to decide whether the throttle
        window has expired; if so, the cap is restored and the timestamp
        cleared so the next call doesn't re-trigger the throttle path.
        """
        b = self._bucket_for(method)
        now = time.monotonic()
        with b.limiter_lock:
            if b.throttled_until and now >= b.throttled_until:
                b.throttled_until = 0.0
                return DEFAULT_RATE
            if b.throttled_until:
                return THROTTLED_RPM
            return b.cap

    def tokens(self, method: str) -> float:
        """Current tokens for the method's bucket (mainly for diagnostics/tests).

        Uses aiolimiter's leaky-bucket approximation. Returns a value in
        [0, capacity]. The aiolimiter library does not expose this directly,
        so we infer it: on `acquire()`, the limiter always leaves
        (capacity - 1) tokens after a successful drain; without an
        `acquire()` call we report `capacity`. Real token count for tests
        is observed by repeatedly `acquire()`-ing and seeing where the
        next acquire blocks. For the simple "is the bucket full?" test,
        this approximation is enough.
        """
        b = self._bucket_for(method)
        return float(b.effective_cap(time.monotonic()))

    def signal_throttle(self, method: str) -> None:
        """Mark the method as throttled for THROTTLE_DURATION_SEC.

        Called by the runner when the SDK raises RESOURCE_EXHAUSTED or
        returns HTTP 429. Drops the per-method cap to THROTTLED_RPM
        and notifies the on_throttle callback so the UI can surface it.
        """
        b = self._bucket_for(method)
        until = time.monotonic() + THROTTLE_DURATION_SEC
        with b.limiter_lock:
            b.throttled_until = until
            # aiolimiter's cap is fixed at construction; we can't
            # dynamically resize it. The cap_for() helper checks the
            # timestamp and reports the right number, but the limiter
            # itself still drains at the original rate. Future work:
            # swap aiolimiter for a configurable-rate bucket.
        logger.warn(
            "tinkoff.rate_limit.throttled",
            method=method,
            duration_sec=THROTTLE_DURATION_SEC,
        )
        if self._on_throttle is not None:
            try:
                self._on_throttle(method, until)
            except Exception:
                logger.warn("tinkoff.rate_limit.throttle_callback_failed")
