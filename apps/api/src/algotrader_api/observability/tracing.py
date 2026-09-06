"""OpenTelemetry tracing setup with OTLP gRPC exporter."""
from __future__ import annotations

import threading
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_lock = threading.Lock()
_initialized = False
_exporter: BatchSpanProcessor | None = None


def setup_tracing(
    *,
    service_name: str,
    otlp_endpoint: str,
    resource_attributes: dict[str, str] | None = None,
) -> None:
    """Configure TracerProvider with OTLP gRPC exporter (idempotent)."""
    global _initialized, _exporter

    with _lock:
        if _initialized:
            return

        attrs = {"service.name": service_name}
        if resource_attributes:
            attrs.update(resource_attributes)

        resource = Resource.create(attrs)
        provider = TracerProvider(resource=resource)

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)

        trace.set_tracer_provider(provider)
        _exporter = processor
        _initialized = True


def get_tracer(name: str = "algotrader_api") -> Any:
    """Return a tracer for the given name."""
    return trace.get_tracer(name)


def shutdown_tracing() -> None:
    """Flush pending spans and shut down the tracer provider."""
    global _initialized
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
    _initialized = False
