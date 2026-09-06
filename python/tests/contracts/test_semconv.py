"""Conformance is tested on exported OTLP, not merely constant names."""

import hashlib
import json
import subprocess
import sys

import jsonschema
import pytest
from conftest import ROOT, begin, connection, response, spans, validate_attributes
from conftest import accumulator as Accumulator
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

import confident_trace as ct
from confident_trace._core.content import ContentPolicy
from confident_trace._semconv import genai_v1_37_0 as ai

REGISTRY = json.loads((ROOT / "spec/genai/1.37.0.json").read_text())
VECTORS = json.loads((ROOT / "spec/genai-emission-vectors.json").read_text())


def test_generated_files_and_provenance():
    subprocess.run(
        [sys.executable, str(ROOT / "tools/generate_genai.py"), "--check"], check=True
    )
    release = json.loads((ROOT / "spec/releases/python-0.1.0.json").read_text())
    assert ct.__version__ == release["version"] == "0.1.0"
    assert REGISTRY["upstream"]["commit"] == "aec6e9d3e86754683dab7c707655d69d953b2768"
    assert REGISTRY["upstream"]["tag"] == "v1.37.0"
    for source in REGISTRY["sources"].values():
        assert hashlib.sha256(source["text"].encode()).hexdigest() == source["sha256"]
    assert any(g["type"] == "metric" for g in REGISTRY["groups"].values())
    assert any(g["type"] == "event" for g in REGISTRY["groups"].values())
    assert REGISTRY["groups"]["span.gen_ai.inference.client"]["attributes"][
        ai.SERVER_PORT
    ]["requirement_level"]
    assert (
        not list((ROOT / "typescript").iterdir())
        if (ROOT / "typescript").exists()
        else True
    )


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["name"])
def test_shared_emission_vectors(telemetry, case):
    _, exporter = telemetry
    operation = begin(case["provider"], case["request"])
    response(operation, case["response"], case["provider"])
    operation.end()
    captured = spans(exporter)
    assert len(captured) == 1
    attrs = dict(captured[0].attributes)
    validate_attributes(attrs)
    for key, value in case["expected"].items():
        assert attrs[key] == (tuple(value) if type(value) is list else value), key
    assert "DO-NOT-CAPTURE" not in str(attrs)
    wire = encode_spans(captured)
    decoded = type(wire).FromString(wire.SerializeToString())
    scope = decoded.resource_spans[0].scope_spans[0]
    assert scope.schema_url == ai.SCHEMA_URL
    assert scope.spans[0].trace_id == captured[0].context.trace_id.to_bytes(16, "big")
    assert {a.key for a in scope.spans[0].attributes} == set(attrs)


@pytest.mark.parametrize(
    "shape,value",
    [
        (
            "input-messages",
            [{"role": "user", "parts": [{"type": "text", "content": "secret" * 1000}]}],
        ),
        (
            "output-messages",
            [
                {
                    "role": "assistant",
                    "finish_reason": "",
                    "parts": [{"type": "text", "content": "🌏" * 1000}],
                }
            ],
        ),
        ("system-instructions", [{"type": "text", "content": "help" * 1000}]),
    ],
)
def test_bounded_content_remains_schema_valid(shape, value):
    for limit in (64, 128, 1024):
        encoded = ContentPolicy(max_bytes=limit).encode(value, shape=shape)
        assert encoded is not None and len(encoded.encode()) <= limit
        jsonschema.validate(json.loads(encoded), REGISTRY["message_schemas"][shape])
    for redactor in (
        lambda _: "invalid",
        lambda _: [{"role": 5, "parts": []}],
        lambda _: 1 / 0,
    ):
        assert ContentPolicy(redact=redactor).encode(value, shape=shape) is None
    assert ContentPolicy(enabled=False).encode(value, shape=shape) is None


def test_stream_choices_tools_usage_and_cap(telemetry):
    _, exporter = telemetry
    op = begin("openai", {"model": "test"})
    accumulate = Accumulator("openai")
    accumulate(
        op,
        {
            "choices": [
                {"index": 0, "delta": {"content": "first"}},
                {
                    "index": 1,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call1",
                                "function": {"name": "lookup", "arguments": '{"city":'},
                            }
                        ]
                    },
                },
            ]
        },
    )
    accumulate(
        op,
        {
            "choices": [
                {
                    "index": 1,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '"Macau"}'}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    )
    accumulate(
        op,
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "x" * 100000},
                    "finish_reason": "stop",
                }
            ]
        },
    )
    accumulate(
        op, {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 20}}
    )
    assert sum(len(c["text"]) for c in accumulate.candidates.values()) <= 4096
    op.end()
    result = spans(exporter)[0]
    validate_attributes(result.attributes)
    output = json.loads(result.attributes[ai.GEN_AI_OUTPUT_MESSAGES])
    assert len(output) == 2
    assert output[0]["finish_reason"] == "stop"
    assert output[1]["finish_reason"] == "tool_call"
    assert output[1]["parts"][0]["arguments"] == {"city": "Macau"}
    assert result.attributes[ai.GEN_AI_USAGE_OUTPUT_TOKENS] == 20
    assert result.attributes["confident.span.content_truncated"] is True


def test_conversation_and_custom_tool_preserve_parent(telemetry):
    _, exporter = telemetry

    @ct.span(kind="tool", name="lookup")
    def lookup(city):
        return city

    with ct.span("answer", thread_id="conversation-42") as entry:
        lookup("Macau")
    child, root = spans(exporter)
    assert child.parent.span_id == entry.get_span_context().span_id
    assert root.context.trace_id == child.context.trace_id
    assert child.attributes[ai.GEN_AI_CONVERSATION_ID] == "conversation-42"
    assert root.attributes[ai.GEN_AI_CONVERSATION_ID] == "conversation-42"
    assert child.name == "execute_tool lookup"
    assert json.loads(child.attributes["confident.span.output"]) == "Macau"
    validate_attributes(child.attributes)


def test_sdk_endpoint_identification():
    from google import genai
    from openai import AzureOpenAI, OpenAI

    with OpenAI(api_key="test", base_url="https://proxy.example:8443/v1") as client:
        assert connection("openai", client.responses) == {
            ai.GEN_AI_PROVIDER_NAME: "openai",
            ai.SERVER_ADDRESS: "proxy.example",
            ai.SERVER_PORT: 8443,
        }
    with AzureOpenAI(
        api_key="test",
        azure_endpoint="https://example.openai.azure.com",
        api_version="2024-06-01",
    ) as client:
        assert (
            connection("openai", client.responses)[ai.GEN_AI_PROVIDER_NAME]
            == "azure.ai.openai"
        )
    with genai.Client(api_key="test") as client:
        assert (
            connection("google_genai", client.models)[ai.GEN_AI_PROVIDER_NAME]
            == "gcp.gemini"
        )


def test_third_party_unknown_vocabulary_is_not_filtered(telemetry):
    provider, exporter = telemetry
    with provider.get_tracer(
        "external", schema_url="https://example.com/future"
    ).start_as_current_span(
        "future",
        attributes={
            "gen_ai.operation.name": "future_operation",
            "gen_ai.future.field": "kept",
        },
    ) as span:
        span.add_event("future.event", {"gen_ai.future.event": 42})
    result = spans(exporter)[0]
    assert result.attributes["gen_ai.future.field"] == "kept"
    assert result.events[0].attributes["gen_ai.future.event"] == 42
    assert result.instrumentation_scope.schema_url == "https://example.com/future"


def test_registry_rebuilds_from_attributed_sources(tmp_path):
    import runpy

    for name, source in REGISTRY["sources"].items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source["text"])
    importer = runpy.run_path(str(ROOT / "tools/import_genai.py"))
    regenerated = importer["snapshot"](
        tmp_path, REGISTRY["version"], REGISTRY["upstream"]["commit"]
    )
    assert regenerated == REGISTRY


def test_google_part_union_and_enum_response(telemetry):
    from conftest import request
    from google.genai import types

    _, exporter = telemetry
    op = begin("google_genai", {"model": "gemini-test"})
    request(
        op,
        "google_genai",
        {
            "model": "gemini-test",
            "contents": [
                types.Part(text="hello"),
                types.Part(
                    inline_data=types.Blob(data=b"binary-secret", mime_type="image/png")
                ),
            ],
            "config": types.GenerateContentConfig(
                max_output_tokens=50,
                response_mime_type="application/json",
                system_instruction="help",
            ),
        },
    )
    response(
        op,
        types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(role="model", parts=[types.Part(text="hi")]),
                    finish_reason=types.FinishReason.STOP,
                )
            ]
        ),
        "google_genai",
    )
    op.end()
    attributes = spans(exporter)[0].attributes
    assert attributes[ai.GEN_AI_REQUEST_MAX_TOKENS] == 50
    assert attributes[ai.GEN_AI_OUTPUT_TYPE] == "json"
    assert attributes[ai.GEN_AI_RESPONSE_FINISH_REASONS] == ("stop",)
    messages = json.loads(attributes[ai.GEN_AI_INPUT_MESSAGES])
    assert len(messages) == 1
    assert messages[0]["parts"][0]["content"] == "hello"
    assert messages[0]["parts"][1]["content_omitted"] is True
    assert "binary-secret" not in str(attributes)


def test_non_recording_provider_never_serializes_content():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

    calls = []
    ct.shutdown()
    ct.init(
        tracer_provider=TracerProvider(sampler=ALWAYS_OFF, shutdown_on_exit=False),
        exporter=InMemorySpanExporter(),
        instrumentations=(),
        redact=lambda value: calls.append(value),
    )
    op = begin(
        "openai", {"model": "test", "messages": [{"role": "user", "content": "secret"}]}
    )
    Accumulator("openai")(op, {"choices": [{"delta": {"content": "secret"}}]})
    op.end()
    ct.shutdown()
    assert calls == []
