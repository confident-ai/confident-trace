import json

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
        == "https://opentelemetry.io/schemas/1.37.0"
    )
