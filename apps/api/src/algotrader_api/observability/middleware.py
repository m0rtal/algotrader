"""FastAPI middleware for latency tracking."""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .correlation import correlation_id
from .logging import get_logger

logger = get_logger("algotrader_api.http")


class LatencyMiddleware(BaseHTTPMiddleware):
    """Measure request duration and log an info event per request."""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response: Response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 3)

        response.headers["X-Latency-Ms"] = str(duration_ms)

        logger.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            correlation_id=correlation_id(),
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return response


# Alias for design.md
observability_middleware = LatencyMiddleware
