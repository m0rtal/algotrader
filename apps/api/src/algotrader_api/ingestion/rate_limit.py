"""Per-method token-bucket rate limiter for Tinkoff SDK calls.

Wraps aiolimiter.AsyncLimiter per method. Default: 14 calls per 60 seconds
(safety margin of 1 below T-Invest API's 15 req/min documented limit).

Each SDK method has its own bucket. acquire() returns a context manager.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import AsyncIterator

from aiolimiter import AsyncLimiter

from ..observability.logging import get_logger

logger = get_logger("algotrader_api.ingestion.rate_limit")

DEFAULT_RATE = 14
DEFAULT_PERIOD = 60.0


@dataclass
class _Bucket:
    limiter: AsyncLimiter
    success_count: int = 0
    error_count: int = 0
    last_error_at: float = 0.0


class RateLimiter:
    """One AsyncLimiter per SDK method. Tracks success/error for adaptive behavior."""

    def __init__(self, rate: int = DEFAULT_RATE, period: float = DEFAULT_PERIOD) -> None:
        self._rate = rate
        self._period = period
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _bucket_for(self, method: str) -> _Bucket:
        with self._lock:
            b = self._buckets.get(method)
            if b is None:
                b = _Bucket(limiter=AsyncLimiter(self._rate, self._period))
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
        import time

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
