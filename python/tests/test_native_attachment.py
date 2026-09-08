"""Embedding is synchronous and does not claim the application's provider."""

from contextvars import ContextVar
from unittest.mock import Mock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from confident_trace._core.attachment import attach_native


def test_native_attachment_keeps_context_and_application_exporter():
    ambient = ContextVar("golden", default=None)
    seen = []

    class Consumer(SpanProcessor):
        def on_start(self, span, parent_context=None):
            seen.append(
                (
                    "start",
                    ambient.get(),
                    span.attributes.get("confident.span.integration"),
                )
            )

        def on_end(self, span):
            seen.append(("end", ambient.get()))

    provider = TracerProvider(shutdown_on_exit=False)
    global_provider = trace.get_tracer_provider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    gate = attach_native(provider, Consumer(), integrations=("strands",))
    token = ambient.set("first")
    try:
        with provider.get_tracer("strands.telemetry.tracer").start_as_current_span(
            "agent"
        ):
            pass
    finally:
        ambient.reset(token)
    assert seen == [("start", "first", "Strands"), ("end", "first")]
    assert trace.get_tracer_provider() is global_provider
    gate.shutdown()
    with provider.get_tracer("application").start_as_current_span("after detach"):
        pass
    assert len(seen) == 2
    assert len(exporter.get_finished_spans()) == 2
    provider.shutdown()


def test_incremental_enablement_and_flush():
    provider = TracerProvider(shutdown_on_exit=False)
    consumer = Mock()
    consumer.force_flush.return_value = False
    gate = attach_native(provider, consumer)
    gate.enable(("pydantic_ai", "google_adk", "agentcore"))
    gate.enable(("pydantic_ai",))
    assert len(gate.integration_scopes) == 2
    assert gate.force_flush(123) is False
    consumer.force_flush.assert_called_once_with(123)
    gate.shutdown()
    gate.shutdown()
    consumer.shutdown.assert_called_once()
    provider.shutdown()


def test_invalid_integration_does_not_attach_processor():
    provider = Mock()
    with pytest.raises(ValueError):
        attach_native(provider, Mock(), integrations=("not-supported",))
    provider.add_span_processor.assert_not_called()
