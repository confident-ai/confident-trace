"""OTel configuration and ownership. No custom transport or span storage."""

from __future__ import annotations

import atexit
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.util.re import parse_env_headers

from ._content import ContentPolicy
from ._genai import SCHEMA_URL
from ._genai import SEMCONV_VERSION as SEMCONV_VERSION

VERSION = "0.1.0"
log = logging.getLogger("confident_trace")
log.addHandler(logging.NullHandler())


def disabled():
    return os.getenv("OTEL_SDK_DISABLED", "").lower() == "true"


class OwnedProcessor(SpanProcessor):
    """A detachable gate around a standard batch processor.

    OTel has no public remove_span_processor API. Closing the gate lets a shared
    provider outlive us without receiving calls on a stopped exporter.
    """

    def __init__(self, delegate):
        self.delegate = delegate
        self.closed = False

    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span):
        if not self.closed:
            try:
                self.delegate.on_end(span)
            except Exception:
                log.debug("Span processor failed")

    def force_flush(self, timeout_millis=30000):
        if self.closed:
            return True
        try:
            return self.delegate.force_flush(timeout_millis)
        except Exception:
            return False

    def shutdown(self):
        if not self.closed:
            self.closed = True
            try:
                self.delegate.shutdown()
            except Exception:
                log.debug("Exporter shutdown failed")


@dataclass
class Runtime:
    provider: object
    policy: ContentPolicy = field(default_factory=ContentPolicy)
    processor: OwnedProcessor | None = None
    active: bool = True
    undo: list = field(default_factory=list)

    def tracer(self):
        if not self.active or disabled():
            return trace.NoOpTracer()
        return self.provider.get_tracer(
            "confident_trace", VERSION, schema_url=SCHEMA_URL
        )


_lock = threading.RLock()
_runtime: Runtime | None = None


def current():
    return _runtime


def tracer():
    return _runtime.tracer() if _runtime else trace.NoOpTracer()


def init(
    *,
    api_key=None,
    endpoint=None,
    protocol=None,
    headers: Mapping[str, str] | None = None,
    timeout=None,
    compression=None,
    tracer_provider=None,
    exporter=None,
    resource_attributes=None,
    capture_content=True,
    max_content_bytes=16384,
    redact=None,
    instrumentations=("openai", "anthropic", "google_genai"),
):
    """Initialize once. Explicit values override OTel environment configuration.

    An explicit endpoint is a traces endpoint (HTTP includes /v1/traces).
    Call shutdown() before changing configuration. Initialization failures disable
    this package and emit a content-free warning rather than breaking the host.
    """
    global _runtime
    with _lock:
        if disabled():
            shutdown()
            return Runtime(trace.NoOpTracerProvider(), active=False)
        if _runtime and _runtime.active:
            return _runtime
        processor = None
        try:
            if max_content_bytes < 64:
                raise ValueError("max_content_bytes must be at least 64")
            provider = (
                tracer_provider
                if tracer_provider is not None
                else trace.get_tracer_provider()
            )
            if isinstance(provider, trace.ProxyTracerProvider):
                provider = TracerProvider(
                    resource=Resource.create(resource_attributes or {}),
                    shutdown_on_exit=False,
                )
                trace.set_tracer_provider(provider)
                provider = trace.get_tracer_provider()
            if not isinstance(provider, TracerProvider):
                raise TypeError("A standard SDK TracerProvider is required")
            if exporter is None:
                selected = (
                    protocol
                    or os.getenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL")
                    or os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL")
                    or "http/protobuf"
                )
                if selected == "http/protobuf":
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                        OTLPSpanExporter,
                    )
                elif selected == "grpc":
                    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                        OTLPSpanExporter,
                    )
                else:
                    raise ValueError("Unsupported OTLP protocol")
                kwargs = {}
                if endpoint is not None:
                    kwargs["endpoint"] = endpoint
                elif not (
                    os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
                    or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
                ):
                    if selected != "http/protobuf":
                        raise ValueError("A gRPC collector endpoint is required")
                    kwargs["endpoint"] = "https://otel.confident-ai.com/v1/traces"
                env_headers = os.getenv(
                    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
                    os.getenv("OTEL_EXPORTER_OTLP_HEADERS", ""),
                )
                resolved = {
                    k.lower(): v
                    for k, v in parse_env_headers(env_headers, liberal=True).items()
                }
                key = api_key if api_key is not None else os.getenv("CONFIDENT_API_KEY")
                if key:
                    resolved["x-confident-api-key"] = key
                resolved.update({k.lower(): v for k, v in (headers or {}).items()})
                kwargs["headers"] = resolved
                if timeout is not None:
                    kwargs["timeout"] = timeout
                if compression is not None:
                    if selected == "http/protobuf":
                        from opentelemetry.exporter.otlp.proto.http import Compression

                        kwargs["compression"] = Compression(compression)
                    else:
                        import grpc

                        kwargs["compression"] = {
                            "gzip": grpc.Compression.Gzip,
                            "none": grpc.Compression.NoCompression,
                        }[compression]
                # Unspecified TLS, compression, timeout and endpoint settings are
                # resolved by the standard exporter, including signal precedence.
                exporter = OTLPSpanExporter(**kwargs)
            processor = OwnedProcessor(BatchSpanProcessor(exporter))
            provider.add_span_processor(processor)
            runtime = Runtime(
                provider,
                ContentPolicy(capture_content, max_content_bytes, redact),
                processor,
            )
            _runtime = runtime
            from ._instrumentation import instrument

            runtime.undo = instrument(instrumentations)
            return runtime
        except Exception:
            if processor:
                processor.shutdown()
            _runtime = Runtime(trace.NoOpTracerProvider(), active=False)
            log.warning("Confident Trace initialization failed; tracing is disabled")
            return _runtime


def flush(timeout_millis=30000):
    rt = _runtime
    return rt.processor.force_flush(timeout_millis) if rt and rt.processor else True


def shutdown(timeout_millis=5000):
    """Stop package instrumentation and wait at most the supplied budget.

    Exporter cleanup runs in a daemon thread because third-party exporters can
    block in shutdown. False means cleanup has not finished within the budget.
    """
    with _lock:
        rt = _runtime
        if not rt or not rt.active:
            return True
        rt.active = False
        for undo in reversed(rt.undo):
            try:
                undo()
            except Exception:
                log.debug("Uninstrumentation failed")
        if not rt.processor:
            return True
        worker = threading.Thread(target=rt.processor.shutdown, daemon=True)
        worker.start()
    worker.join(max(0, timeout_millis) / 1000)
    return not worker.is_alive()


def _after_fork():
    global _lock
    _lock = threading.RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)
atexit.register(shutdown)
