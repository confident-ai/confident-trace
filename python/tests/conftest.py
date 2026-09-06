import json
from pathlib import Path

import jsonschema
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace import _genai as ai

REGISTRY = json.loads(
    (Path(__file__).resolve().parents[2] / "spec/genai/1.37.0.json").read_text()
)


@pytest.fixture
def telemetry():
    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    ct.init(tracer_provider=provider, exporter=exporter, instrumentations=())
    yield provider, exporter
    ct.shutdown()


def spans(exporter):
    ct.flush()
    captured = exporter.get_finished_spans()
    for span in captured:
        if span.instrumentation_scope.name == "confident_trace":
            validate_attributes(span.attributes)
    return captured


def validate_attributes(attributes):
    for key, value in attributes.items():
        if key not in REGISTRY["attributes"]:
            assert not key.startswith(("gen_ai.", "openai.")), key
            continue
        kind = REGISTRY["attributes"][key]["type"]
        if isinstance(kind, dict):
            assert isinstance(value, str)  # Enum lists are intentionally open.
        elif kind == "int":
            assert type(value) is int
        elif kind == "double":
            assert type(value) in (float, int)
        elif kind == "string":
            assert type(value) is str
        elif kind == "string[]":
            assert isinstance(value, (list, tuple)) and all(
                type(v) is str for v in value
            )
    for key, schema in (
        (ai.GEN_AI_INPUT_MESSAGES, "input-messages"),
        (ai.GEN_AI_OUTPUT_MESSAGES, "output-messages"),
        (ai.GEN_AI_SYSTEM_INSTRUCTIONS, "system-instructions"),
    ):
        if key in attributes:
            jsonschema.validate(
                json.loads(attributes[key]), REGISTRY["message_schemas"][schema]
            )
