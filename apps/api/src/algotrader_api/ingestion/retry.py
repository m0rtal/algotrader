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

# Pattern for Tinkoff server-suggested retry delay. Server returns this
# in the gRPC trailing metadata as `ratelimit_reset=<seconds>`; we also
# see it in the string representation of the RuntimeError raised by the
# SDK. Matching both lets the helper work without depending on the grpc
# module at import time.
import re as _re

_RATELIMIT_RESET_RE = _re.compile(
    r"ratelimit_reset\s*[=:]\s*(\d+)", _re.IGNORECASE
)


class _BackoffState:
    def __init__(self) -> None:
        self.consecutive_success_started: float | None = None
        self.last_failure_at: float = 0.0


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Return True if exception looks like a rate-limit / quota error."""
    msg = str(exc).lower()
    return any(p.lower() in msg for p in _RESOURCE_EXHAUSTED_PATTERNS)


def _extract_retry_after(exc: BaseException) -> float | None:
    """Read server-suggested retry delay from a rate-limit error.

    Tinkoff sends `ratelimit_reset=<seconds-until-window-rolls>` in the
    gRPC metadata for RESOURCE_EXHAUSTED responses; respecting that
    instead of using a fixed exponential backoff avoids flooding the
    server with retries that are guaranteed to fail until the window
    rolls over.

    Returns the suggested delay in seconds (always >= 1 so a tiny
    reset value doesn't cause a hot loop), or None when the metadata
    is not present. Handles both RuntimeError str representations
    (what our SDK currently raises) and live grpc.RpcError objects.
    """
    delay: float | None = None
    # 1) String match — covers the SDK's RuntimeError("...Metadata(...)") form.
    m = _RATELIMIT_RESET_RE.search(str(exc))
    if m:
        delay = float(m.group(1))
    # 2) Live grpc.RpcError — has trailing_metadata() returning an iterable.
    trailing = getattr(exc, "trailing_metadata", None)
    if trailing is not None and callable(trailing):
        try:  # pragma: no cover — defensive against any grpc stub mismatch
            items = trailing()
            for entry in list(items):  # type: ignore[arg-type]
                if not isinstance(entry, tuple) or len(entry) != 2:
                    continue
                key, value = entry
                if str(key).lower() == "ratelimit_reset":
                    delay = float(value)  # type: ignore[arg-type]
                    break
        except Exception:
            pass
    if delay is None:
        return None
    return max(delay, 1.0)


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
                # If the server told us exactly when the rate-limit
                # window rolls over, honour that instead of relying on
                # the local exponential schedule.
                server_suggested = _extract_retry_after(e)
                if server_suggested is not None:
                    delay = server_suggested
                logger.info(
                    "retry.backoff",
                    attempt=attempt,
                    next_delay_s=round(delay, 3),
                    error=str(e),
                    server_suggested=server_suggested,
                )
                await asyncio.sleep(delay)
                # When the server told us exactly how long to wait, use
                # that as the seed for the next backoff (so a tight
                # exponential doesn't drown it out). Otherwise let the
                # local exponential schedule continue normally.
                if server_suggested is not None:
                    delay = max(delay, server_suggested) * self._backoff_factor
                    delay = min(delay, self._max_delay)
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
