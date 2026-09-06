"""Tests for observability module: correlation_id, scrubbers, spans."""
from __future__ import annotations

from algotrader_api.observability.correlation import (
    CORRELATION_ID_HEADER,
    correlation_id,
    generate_correlation_id,
    set_correlation_id,
)
from algotrader_api.observability.scrubbers import (
    make_drop_health_noisy,
    mask_account_id,
    process_event,
    redact_token_fields,
    truncate_long_strings,
)


def test_generate_correlation_id_returns_uuid_string():
    cid = generate_correlation_id()
    assert isinstance(cid, str)
    assert len(cid) >= 32  # UUID4 has 32 hex chars + dashes


def test_set_and_get_correlation_id():
    set_correlation_id("test-123")
    assert correlation_id() == "test-123"


def test_redact_token_fields():
    out = redact_token_fields(None, "info", {"token": "secret", "name": "ok", "Token": "x"})
    assert out["token"] == "[REDACTED]"
    assert out["Token"] == "[REDACTED]"
    assert out["name"] == "ok"


def test_redact_password_secret_api_key():
    out = redact_token_fields(None, "info", {"password": "p", "secret": "s", "api_key": "k", "apiKey": "k2"})
    assert all(out[k] == "[REDACTED]" for k in ["password", "secret", "api_key", "apiKey"])


def test_mask_account_id():
    out = mask_account_id(None, "info", {"accountId": "ACC-123456789", "account_id": "X-987654"})
    assert out["accountId"] == "AC***89"
    assert out["account_id"] == "X-***54"


def test_mask_account_id_short_unchanged():
    out = mask_account_id(None, "info", {"accountId": "AB"})
    assert out["accountId"] == "AB"  # too short, unchanged


def test_truncate_long_strings():
    long_str = "x" * 2000
    out = truncate_long_strings(None, "info", {"description": long_str})
    assert len(out["description"]) < 1024
    assert "truncated" in out["description"]


def test_truncate_short_strings_unchanged():
    out = truncate_long_strings(None, "info", {"description": "short"})
    assert out["description"] == "short"


def test_drop_health_noisy_drops():
    drop = make_drop_health_noisy(0.0)  # sample 0% = always drop
    out = drop(None, "info", {"path": "/health", "status": 200})
    assert out == {}


def test_drop_health_noisy_drops_readyz():
    drop = make_drop_health_noisy(0.0)
    out = drop(None, "info", {"path": "/readyz", "status": 200})
    assert out == {}


def test_drop_health_noisy_drops_when_health_route():
    drop = make_drop_health_noisy(0.0)
    out = drop(None, "info", {"http.route": "/health", "status": 200})
    assert out == {}


def test_drop_health_noisy_keeps_when_level_error():
    drop = make_drop_health_noisy(0.0)
    out = drop(None, "info", {"path": "/health", "status": 200, "level": "error"})
    assert out.get("level") == "error"


def test_drop_health_noisy_keeps_errors():
    drop = make_drop_health_noisy(0.0)
    out = drop(None, "info", {"path": "/health", "status": 500})
    assert out.get("status") == 500


def test_drop_health_noisy_keeps_non_health():
    drop = make_drop_health_noisy(0.0)
    out = drop(None, "info", {"path": "/api/kpis", "status": 200})
    assert out.get("status") == 200


def test_process_event_applies_all_scrubbers():
    out = process_event(None, "info", {
        "token": "secret",
        "accountId": "ACC-999999999",
        "path": "/api/kpis",
    }, health_sample_rate=1.0)
    assert out["token"] == "[REDACTED]"
    assert out["accountId"] == "AC***99"


def test_correlation_id_header_constant():
    assert CORRELATION_ID_HEADER == "X-Correlation-ID"


def test_logging_setup_is_idempotent():
    """setup_logging uses a module-level lock — second call should be a no-op."""
    from algotrader_api.observability import logging as obs_logging

    obs_logging.setup_logging(level="INFO", health_sample_rate=1.0)
    obs_logging.setup_logging(level="DEBUG", health_sample_rate=0.5)  # second call
    # Should still work after second call
    logger = obs_logging.get_logger("test")
    assert logger is not None


def test_logging_console_format():
    """log_format='console' uses ConsoleRenderer instead of JSONRenderer."""
    from algotrader_api.observability import logging as obs_logging

    obs_logging.setup_logging(level="INFO", log_format="console", health_sample_rate=1.0)
    logger = obs_logging.get_logger("test")
    logger.info("test_event", key="value")  # no exception


def test_logging_with_active_otel_span(caplog):
    """When inside an active OTel span, log events should include trace_id/span_id."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("test")

    from algotrader_api.observability import logging as obs_logging

    obs_logging.setup_logging(level="INFO", health_sample_rate=1.0)
    logger = obs_logging.get_logger("test")

    with tracer.start_as_current_span("test-span"):
        logger.info("inside_span", key="value")
    logger.info("outside_span", key="value")  # should not raise


def test_logging_handles_special_chars(caplog):
    """Structlog processor chain handles text with quotes, newlines, unicode."""
    from algotrader_api.observability import logging as obs_logging

    obs_logging.setup_logging(level="INFO", health_sample_rate=1.0)
    logger = obs_logging.get_logger("test")
    logger.info("special", message='line1\nline2\ttab"quoted"', unicode_test="héllo 日本語")
