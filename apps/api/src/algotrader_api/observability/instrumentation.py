"""Manual span wrappers for DB queries."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode


@contextmanager
def instrument_db_query(
    *,
    db_system: str,
    statement: str,
    statement_truncate: int = 256,
) -> Iterator[Any]:
    """Wrap a DB query execution in a span with db.* attributes.

    Yields the active span so callers can set additional attributes (e.g. row_count).
    """
    tracer = trace.get_tracer("algotrader_api.db")
    truncated = statement if len(statement) <= statement_truncate else statement[:statement_truncate] + "..."

    with tracer.start_as_current_span(f"db.query.{db_system}") as span:
        span.set_attribute("db.system", db_system)
        span.set_attribute("db.statement", truncated)
        start = time.perf_counter()
        try:
            yield span
        except Exception as e:
            span.set_status(Status(StatusCode.ERROR, str(e)))
            span.record_exception(e)
            raise
        finally:
            span.set_attribute("db.duration_ms", round((time.perf_counter() - start) * 1000, 3))
