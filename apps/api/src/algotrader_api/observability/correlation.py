"""Correlation ID context variable and middleware."""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_ID_HEADER = "X-Correlation-ID"
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str | None) -> None:
    _correlation_id.set(value)


def clear_correlation_id() -> None:
    _correlation_id.set(None)


def generate_correlation_id() -> str:
    """Generate a UUID for correlation. UUIDv4 used (Python 3.14+ supports uuid7 natively)."""
    return str(uuid.uuid4())


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Read or generate X-Correlation-ID; expose via contextvar and response header."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(CORRELATION_ID_HEADER)
        cid = incoming if incoming else generate_correlation_id()
        token = _correlation_id.set(cid)
        try:
            response: Response = await call_next(request)
        finally:
            _correlation_id.reset(token)
        response.headers[CORRELATION_ID_HEADER] = cid
        return response


# Alias for backwards compatibility with design.md
correlation_middleware = CorrelationMiddleware
