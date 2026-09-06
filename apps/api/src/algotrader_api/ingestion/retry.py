"""Adaptive retry with exponential backoff for SDK calls.

Detects RESOURCE_EXHAUSTED (gRPC code 8) and HTTP 429, applies exponential
backoff. Resets on successful 2xx response.

Strategy:
- max_attempts: 5 (then abandon and let caller mark phase=err)
- initial_delay: 1.0s
- backoff_factor: 2.0
- max_delay: 60.0s (cap)
- reset_after_success: 5 minutes of 2xx clears backoff state
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, TypeVar

from ..observability.logging import get_logger

logger = get_logger("algotrader_api.ingestion.retry")

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_INITIAL_DELAY = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_MAX_DELAY = 60.0
DEFAULT_RESET_AFTER = 300.0  # seconds of consecutive 2xx to reset backoff

# Strings/patterns that indicate RESOURCE_EXHAUSTED in error messages.
_RESOURCE_EXHAUSTED_PATTERNS = (
    "RESOURCE_EXHAUSTED",
    "rate limit",
    "too many requests",
    "429",
)


class _BackoffState:
    def __init__(self) -> None:
        self.consecutive_success_started: float | None = None
        self.last_failure_at: float = 0.0


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Return True if exception looks like a rate-limit / quota error."""
    msg = str(exc).lower()
    return any(p.lower() in msg for p in _RESOURCE_EXHAUSTED_PATTERNS)


class AdaptiveRetry:
    """Per-call adaptive retry policy with exponential backoff.

    Not thread-safe — designed for single async loop. The worker uses one
    instance per phase.
    """

    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        initial_delay: float = DEFAULT_INITIAL_DELAY,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        max_delay: float = DEFAULT_MAX_DELAY,
        reset_after: float = DEFAULT_RESET_AFTER,
    ) -> None:
        self._max_attempts = max_attempts
        self._initial_delay = initial_delay
        self._backoff_factor = backoff_factor
        self._max_delay = max_delay
        self._reset_after = reset_after
        self._state = _BackoffState()

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @property
    def last_failure_at(self) -> float:
        return self._state.last_failure_at

    async def run(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Run fn() with adaptive retry. Raises the last exception if all attempts fail."""
        delay = self._initial_delay
        last_exc: BaseException | None = None
        for attempt in range(1, self._max_attempts + 1):
            # Reset backoff if we've had a long enough streak of successes
            self._maybe_reset_backoff()
            try:
                result = await fn()
                self._record_success()
                return result
            except Exception as e:
                last_exc = e
                self._state.last_failure_at = time.time()
                if attempt == self._max_attempts:
                    logger.warning(
                        "retry.exhausted",
                        attempts=attempt,
                        error=str(e),
                    )
                    raise
                if not _is_rate_limit_error(e):
                    logger.warning(
                        "retry.non_rate_error",
                        attempts=attempt,
                        error=str(e),
                    )
                    raise
                logger.info(
                    "retry.backoff",
                    attempt=attempt,
                    next_delay_s=round(delay, 3),
                    error=str(e),
                )
                await asyncio.sleep(delay)
                delay = min(delay * self._backoff_factor, self._max_delay)
        # Unreachable, but mypy complains without this
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("unreachable")

    def _record_success(self) -> None:
        if self._state.consecutive_success_started is None:
            self._state.consecutive_success_started = time.time()

    def _maybe_reset_backoff(self) -> None:
        if self._state.consecutive_success_started is None:
            return
        elapsed = time.time() - self._state.consecutive_success_started
        if elapsed >= self._reset_after:
            self._state.consecutive_success_started = time.time()
            self._state.last_failure_at = 0.0
