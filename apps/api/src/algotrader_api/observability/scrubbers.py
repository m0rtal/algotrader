"""Structlog processors for redaction and sampling."""
from __future__ import annotations

import random
from typing import Any

# Field names that should always be redacted (case-insensitive)
SENSITIVE_FIELDS = frozenset({"token", "tokenlast4", "password", "secret", "authorization", "api_key", "apikey"})

# Keys that indicate account IDs — get partial mask
ACCOUNT_FIELDS = frozenset({"accountid", "account_id"})

# Health check path markers — sampled separately
_HEALTH_PATHS = ("/health", "/healthz", "/ready", "/readyz")


def redact_token_fields(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Replace sensitive field values with [REDACTED]."""
    for key in list(event_dict.keys()):
        if key.lower() in SENSITIVE_FIELDS:
            event_dict[key] = "[REDACTED]"
    return event_dict


def mask_account_id(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Partially mask account ID fields: 'ACC-123456789' -> 'AC***89'."""
    for key in list(event_dict.keys()):
        if key.lower() in ACCOUNT_FIELDS and isinstance(event_dict[key], str) and len(event_dict[key]) >= 4:
            v = event_dict[key]
            event_dict[key] = f"{v[:2]}***{v[-2:]}"
    return event_dict


def truncate_long_strings(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Truncate string values > 1KB to 512 chars + marker."""
    for key, value in list(event_dict.items()):
        if isinstance(value, str) and len(value) > 1024:
            event_dict[key] = value[:512] + "...truncated"
    return event_dict


HEALTH_SAMPLE_RATE = 0.1


def make_drop_health_noisy(sample_rate: float = HEALTH_SAMPLE_RATE):
    """Return a structlog processor that drops health check events with `1 - sample_rate` probability.

    Signals drop by returning an empty dict — structlog treats empty dict as drop signal.
    """
    rng = random.Random()

    def drop_health_noisy(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        path = event_dict.get("path") or event_dict.get("http.route") or ""
        is_health = isinstance(path, str) and any(p in path for p in _HEALTH_PATHS)
        is_error = event_dict.get("level") in ("error", "critical") or (
            isinstance(event_dict.get("status"), int) and event_dict["status"] >= 400
        )
        if is_health and not is_error and rng.random() > sample_rate:
            return {}  # signal drop via empty dict (structlog convention)
        return event_dict

    return drop_health_noisy


# Public entrypoint used by logging.py
def process_event(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
    *,
    health_sample_rate: float = HEALTH_SAMPLE_RATE,
) -> dict[str, Any]:
    """Apply all scrubbers + health sampling in one pass."""
    event_dict = redact_token_fields(logger, method_name, event_dict)
    event_dict = mask_account_id(logger, method_name, event_dict)
    event_dict = truncate_long_strings(logger, method_name, event_dict)
    if health_sample_rate < 1.0:
        drop = make_drop_health_noisy(health_sample_rate)
        event_dict = drop(logger, method_name, event_dict)
    return event_dict
