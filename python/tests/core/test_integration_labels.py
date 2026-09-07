"""Integration identity survives nesting, failures, and standard OTLP encoding."""

import pytest
from conftest import begin, spans
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

import confident_trace as ct
from confident_trace import _attributes as attrs
from confident_trace._core import runtime
from confident_trace._core.spans import Operation
from confident_trace.integrations._shared.execution import State


@pytest.mark.parametrize("integration", list(ct.Integration))
def test_labels_are_plain_strings_on_wire(telemetry, integration):
    _, exporter = telemetry
    op = Operation("integration", integration=integration)
    op.end()
    wire = encode_spans(spans(exporter))
    span = wire.resource_spans[0].scope_spans[0].spans[0]
    assert (
        next(
            a.value.string_value
            for a in span.attributes
            if a.key == attrs.SPAN_INTEGRATION
        )
        == integration.value
    )
    assert type(spans(exporter)[0].attributes[attrs.SPAN_INTEGRATION]) is str


@pytest.mark.parametrize(
    "provider,label",
    [
        ("openai", "OpenAI"),
        ("anthropic", "Anthropic"),
        ("google_genai", "Google GenAI"),
    ],
)
def test_provider_identity_inside_framework_on_failure(telemetry, provider, label):
    _, exporter = telemetry
    parent = Operation("framework", integration=ct.Integration.LANGCHAIN)
    with parent.active():
        op = begin(provider, {"model": "test"})
        op.end(ValueError("failure"))
    parent.end()
    child, framework = spans(exporter)
    assert child.attributes[attrs.SPAN_INTEGRATION] == label
    assert framework.attributes[attrs.SPAN_INTEGRATION] == "LangChain"
    assert child.parent.span_id == framework.context.span_id
    assert child.status.status_code.name == "ERROR"


def test_execution_stamp_wins_over_described_attributes(telemetry):
    _, exporter = telemetry
    state = State(runtime.current(), integration=ct.Integration.LLAMAINDEX)
    op = state.start("agent", {attrs.SPAN_INTEGRATION: "incorrect"})
    op.end()
    assert spans(exporter)[0].attributes[attrs.SPAN_INTEGRATION] == "LlamaIndex"
    state.close()


def test_native_scope_stamp_and_shutdown(telemetry, monkeypatch):
    from confident_trace.integrations.google_adk import instrumentation
    from confident_trace.integrations.google_adk._constants import SCOPE_NAME

    monkeypatch.setattr(instrumentation, "distribution", lambda _: object())
    provider, exporter = telemetry
    undo = instrumentation.instrument(runtime.current())
    try:
        tracer = provider.get_tracer(
            SCOPE_NAME, schema_url="https://example.org/native"
        )
        with tracer.start_as_current_span(
            "native", attributes={"native.field": "kept"}
        ) as s:
            s.add_event("native-event")
        with provider.get_tracer("unrelated").start_as_current_span("unrelated"):
            pass
        native, unrelated = spans(exporter)
        assert native.attributes[attrs.SPAN_INTEGRATION] == "Google ADK"
        assert native.attributes["native.field"] == "kept"
        assert native.events[0].name == "native-event"
        assert native.instrumentation_scope.schema_url == "https://example.org/native"
        assert attrs.SPAN_INTEGRATION not in unrelated.attributes
        monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
        with tracer.start_as_current_span("disabled") as disabled:
            assert attrs.SPAN_INTEGRATION not in disabled.attributes
        monkeypatch.delenv("OTEL_SDK_DISABLED")
        ct.shutdown()
        with tracer.start_as_current_span("after shutdown") as after:
            assert attrs.SPAN_INTEGRATION not in after.attributes
    finally:
        for restore in undo:
            restore()
