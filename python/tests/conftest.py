import json
from pathlib import Path

import jsonschema
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace._semconv import genai_v1_37_0 as ai

ROOT = Path(__file__).resolve().parents[2]

REGISTRY = json.loads((ROOT / "spec/genai/1.37.0.json").read_text())


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


def enable(telemetry, name):
    provider, exporter = telemetry
    ct.shutdown()
    # shutdown closes the original in-memory exporter; use a fresh one.
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    ct.init(tracer_provider=provider, exporter=exporter, instrumentations=(name,))
    return exporter


def integration(name, module):
    from importlib import import_module

    return import_module(f"confident_trace.integrations.{name}.{module}")


def begin(provider, params, instance=None):
    return integration(provider, "instrumentation").begin(params, instance)


def response(op, value, provider):
    return integration(provider, "extraction").response(op, value)


def request(op, provider, params):
    return integration(provider, "extraction").request(op, params)


def connection(provider, instance):
    return integration(provider, "extraction").connection(instance)


def accumulator(provider):
    return integration(provider, "streaming").Accumulator()
