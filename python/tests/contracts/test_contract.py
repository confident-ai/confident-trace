import json

import pytest
from conftest import ROOT, spans
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

import confident_trace as ct


def test_shared_vectors_are_standard_otlp(telemetry):
    provider, exporter = telemetry
    vectors = json.loads((ROOT / "spec/genai-vectors.json").read_text())
    assert vectors["emitted_semconv"] == ct.SEMCONV_VERSION
    for case in vectors["cases"]:
        with provider.get_tracer("third-party").start_as_current_span(
            case["name"], attributes=case["attributes"]
        ) as span:
            for event in case["events"]:
                span.add_event(event["name"], event["attributes"])
    wire = encode_spans(spans(exporter))
    decoded = type(wire).FromString(wire.SerializeToString())
    emitted = [
        span
        for resource in decoded.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]
    assert [s.name for s in emitted] == [c["name"] for c in vectors["cases"]]
    assert len(emitted[1].events) == 2


def test_emitted_schema_version(telemetry):
    _, exporter = telemetry
    with ct.span("example"):
        pass
    wire = encode_spans(spans(exporter))
    assert (
        wire.resource_spans[0].scope_spans[0].schema_url
        == "https://opentelemetry.io/schemas/1.41.0"
    )


SPAN_EXPORT = json.loads((ROOT / "spec/span-export-vectors.json").read_text())


def run_span_export_case(case, export_non_ai_spans):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=provider,
        exporter=exporter,
        instrumentations=(),
        export_non_ai_spans=export_non_ai_spans,
    )
    remote = SPAN_EXPORT["remote_parent"]
    remote_parent = NonRecordingSpan(
        SpanContext(
            int(remote["trace_id"], 16),
            int(remote["span_id"], 16),
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
    )
    started = {}
    try:
        for spec in case["spans"]:
            scope = "confident_trace" if spec["scope"] == "@sdk" else spec["scope"]
            parent = spec.get("parent")
            parent_span = remote_parent if parent == "@remote" else started.get(parent)
            span = provider.get_tracer(scope).start_span(
                spec["name"],
                context=trace.set_span_in_context(parent_span) if parent_span else None,
                attributes=spec.get("attributes"),
            )
            for event in spec.get("events", ()):
                span.add_event(event)
            started[spec["name"]] = span
        for name in case["end"]:
            started[name].end()
        exported = spans(exporter)
        for span in exported:
            assert span.parent == started[span.name].parent
        return [s.name for s in exported]
    finally:
        ct.shutdown()


@pytest.mark.parametrize("case", SPAN_EXPORT["cases"], ids=lambda c: c["name"])
def test_span_export_vectors(case):
    assert run_span_export_case(case, export_non_ai_spans=False) == case["exported"]


@pytest.mark.parametrize("case", SPAN_EXPORT["cases"], ids=lambda c: c["name"])
def test_span_export_vectors_with_non_ai_spans(case):
    assert run_span_export_case(case, export_non_ai_spans=True) == case["end"]
