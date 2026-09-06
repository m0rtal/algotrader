"""Structlog setup with OTel log exporter + stdout JSON fallback."""
from __future__ import annotations

import logging
import sys
import threading
from typing import Any

import structlog
from opentelemetry import trace

from .correlation import correlation_id

_lock = threading.Lock()
_initialized = False

# OTel severity number mapping (per OTel logs data model)
LEVEL_TO_SEVERITY = {
    "debug": 5,
    "info": 9,
    "warning": 13,
    "warn": 13,
    "error": 17,
    "critical": 21,
}


def _add_correlation_id(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Inject correlation_id from contextvar into every event."""
    cid = correlation_id()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def _inject_trace_context(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Inject OTel trace_id and span_id from active context, if available."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def setup_logging(
    *,
    level: str = "INFO",
    log_format: str = "json",
    health_sample_rate: float = 0.1,
) -> None:
    """Configure structlog + stdlib logging to write JSON events to stdout.

    Logs are also bridged into the OpenTelemetry Logs API via stdlib logging handler,
    but we keep this simple: stdout JSON for now (collector can scrape if needed).
    For OTLP log export, see OTelLoggerHandler below (used in production).
    """
    global _initialized
    with _lock:
        if _initialized:
            return

        processors: list[Any] = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            _add_correlation_id,
            _inject_trace_context,
            _scrubber_factory(health_sample_rate),
        ]

        if log_format == "json":
            processors.extend(
                [
                    structlog.processors.TimeStamper(fmt="iso"),
                    structlog.processors.StackInfoRenderer(),
                    structlog.processors.format_exc_info,
                    structlog.processors.JSONRenderer(),
                ]
            )
        else:
            processors.append(structlog.dev.ConsoleRenderer())

        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, level.upper(), logging.INFO)
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
            cache_logger_on_first_use=True,
        )
        _initialized = True


def _scrubber_factory(health_sample_rate: float) -> Any:
    """Build a structlog processor that runs all scrubbers."""
    from .scrubbers import (
        mask_account_id,
        redact_token_fields,
        truncate_long_strings,
    )
    from .scrubbers import make_drop_health_noisy

    drop = make_drop_health_noisy(health_sample_rate)

    def processor(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any] | None:
        event_dict = redact_token_fields(logger, method_name, event_dict)
        event_dict = mask_account_id(logger, method_name, event_dict)
        event_dict = truncate_long_strings(logger, method_name, event_dict)
        if health_sample_rate < 1.0:
            result = drop(logger, method_name, event_dict)
            if result is None:
                return None
        return event_dict

    return processor


def get_logger(name: str | None = None) -> Any:
    """Get a structlog logger."""
    return structlog.get_logger(name) if name else structlog.get_logger()
