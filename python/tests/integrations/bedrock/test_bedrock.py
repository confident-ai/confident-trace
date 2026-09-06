import json
import struct
import zlib
from contextlib import closing
from types import SimpleNamespace

import boto3
import pytest
from botocore.eventstream import EventStream
from botocore.exceptions import ClientError, EventStreamError
from botocore.parsers import EventStreamJSONParser
from botocore.stub import Stubber
from conftest import ROOT, enable, spans

import confident_trace as ct
from confident_trace._core.spans import Operation
from confident_trace._semconv import genai_v1_37_0 as ai
from confident_trace.integrations.bedrock.extraction import request, response


@pytest.fixture
def client():
    with closing(
        boto3.client(
            "bedrock-runtime",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
    ) as client:
        yield client


def params():
    return {
        "modelId": "test-model",
        "messages": [{"role": "user", "content": [{"text": "hello"}]}],
        "inferenceConfig": {
            "maxTokens": 50,
            "temperature": 0.5,
            "topP": 0.9,
            "stopSequences": ["END"],
        },
    }


def result():
    return {
        "output": {
            "message": {"role": "assistant", "content": [{"text": "hello back"}]}
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 5, "outputTokens": 2, "totalTokens": 7},
        "metrics": {"latencyMs": 1},
        "ResponseMetadata": {"RequestId": "request-1"},
    }


def test_converse_standard_client_and_parent(telemetry, client):
    exporter = enable(telemetry, "bedrock")
    expected = result()
    with Stubber(client) as stub:
        stub.add_response("converse", expected, params())
        with ct.span("answer", thread_id="conversation-42") as root:
            actual = client.converse(**params())
            assert actual is expected
    child, parent = spans(exporter)
    assert child.parent == root.get_span_context()
    assert child.attributes[ai.GEN_AI_PROVIDER_NAME] == "aws.bedrock"
    assert child.attributes[ai.GEN_AI_CONVERSATION_ID] == "conversation-42"
    assert (
        child.attributes[ai.SERVER_ADDRESS] == "bedrock-runtime.us-east-1.amazonaws.com"
    )
    assert child.attributes[ai.SERVER_PORT] == 443
    assert child.attributes[ai.GEN_AI_REQUEST_MAX_TOKENS] == 50
    assert child.attributes[ai.GEN_AI_REQUEST_STOP_SEQUENCES] == ("END",)
    assert child.attributes[ai.GEN_AI_USAGE_INPUT_TOKENS] == 5
    assert child.attributes[ai.GEN_AI_RESPONSE_ID] == "request-1"
    assert child.attributes[ai.GEN_AI_RESPONSE_FINISH_REASONS] == ("stop",)
    assert "confident.trace.input" not in child.attributes


def frame(name, payload, message_type="event"):
    headers = b""
    for key, value in {
        ":message-type": message_type,
        ":event-type" if message_type == "event" else ":exception-type": name,
        ":content-type": "application/json",
    }.items():
        key, value = key.encode(), value.encode()
        headers += (
            bytes([len(key)]) + key + b"\x07" + struct.pack(">H", len(value)) + value
        )
    body = json.dumps(payload).encode()
    prelude = struct.pack(">II", 16 + len(headers) + len(body), len(headers))
    message = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + body
    return message + struct.pack(">I", zlib.crc32(message))


class RawStream:
    def __init__(self, frames):
        self.frames = frames
        self.closed = False
        self.reads = 0

    def stream(self):
        for item in self.frames:
            self.reads += 1
            yield item

    def close(self):
        self.closed = True


def stream_response(client, monkeypatch, frames):
    raw = RawStream(frames)
    shape = client.meta.service_model.operation_model(
        "ConverseStream"
    ).output_shape.members["stream"]
    stream = EventStream(raw, shape, EventStreamJSONParser(), "ConverseStream")
    result = {"stream": stream, "ResponseMetadata": {"RequestId": "stream-1"}}
    monkeypatch.setattr(
        client._endpoint,
        "make_request",
        lambda *args, **kwargs: (SimpleNamespace(status_code=200), result),
    )
    return raw, result


@pytest.mark.parametrize("ending", ["exhaust", "close", "error"])
def test_converse_eventstream_lifecycle(telemetry, client, monkeypatch, ending):
    exporter = enable(telemetry, "bedrock")
    frames = [
        frame(
            "contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "partial"}}
        )
    ]
    if ending == "error":
        frames += [
            frame(
                "modelStreamErrorException", {"message": "private error"}, "exception"
            )
        ]
    else:
        frames += [
            frame("messageStop", {"stopReason": "end_turn"}),
            frame(
                "metadata",
                {
                    "usage": {"inputTokens": 5, "outputTokens": 2, "totalTokens": 7},
                    "metrics": {"latencyMs": 1},
                },
            ),
        ]
    raw, expected = stream_response(client, monkeypatch, frames)
    actual = client.converse_stream(**params())
    assert actual is expected
    iterator = iter(actual["stream"])
    assert next(iterator)["contentBlockDelta"]["delta"]["text"] == "partial"
    assert raw.reads == 1
    assert spans(exporter) == ()  # Span remains open while the application streams.
    if ending == "error":
        with pytest.raises(EventStreamError):
            next(iterator)
    elif ending == "exhaust":
        assert len(list(iterator)) == 2
    actual["stream"].close()
    assert raw.closed
    captured = spans(exporter)
    assert len(captured) == 1
    attrs = captured[0].attributes
    assert attrs[ai.GEN_AI_RESPONSE_ID] == "stream-1"
    output = json.loads(attrs[ai.GEN_AI_OUTPUT_MESSAGES])
    assert output[0]["parts"][0]["content"] == "partial"
    assert output[0]["finish_reason"] == ("stop" if ending == "exhaust" else "")
    if ending == "exhaust":
        assert attrs[ai.GEN_AI_USAGE_OUTPUT_TOKENS] == 2
    elif ending == "error":
        assert attrs["error.type"] == "EventStreamError"
        assert "private error" not in str(attrs)


def test_stream_tool_deltas_and_usage_after_cap(telemetry, client, monkeypatch):
    exporter = enable(telemetry, "bedrock")
    frames = [
        frame(
            "contentBlockStart",
            {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "t1", "name": "lookup"}},
            },
        ),
        frame(
            "contentBlockDelta",
            {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"city":'}}},
        ),
        frame(
            "contentBlockDelta",
            {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '"Macau"}'}}},
        ),
        frame(
            "contentBlockDelta",
            {"contentBlockIndex": 1, "delta": {"text": "x" * 100000}},
        ),
        frame("messageStop", {"stopReason": "tool_use"}),
        frame(
            "metadata",
            {
                "usage": {"inputTokens": 10, "outputTokens": 99, "totalTokens": 109},
                "metrics": {"latencyMs": 1},
            },
        ),
    ]
    _, _ = stream_response(client, monkeypatch, frames)
    stream = client.converse_stream(**params())["stream"]
    assert len(list(stream)) == 6
    stream.close()
    attrs = spans(exporter)[0].attributes
    assert attrs[ai.GEN_AI_USAGE_OUTPUT_TOKENS] == 99
    assert attrs["confident.span.content_truncated"] is True
    output = json.loads(attrs[ai.GEN_AI_OUTPUT_MESSAGES])[0]
    tool = next(p for p in output["parts"] if p["type"] == "tool_call")
    assert tool["arguments"] == {"city": "Macau"}
    assert output["finish_reason"] == "tool_call"


def test_bedrock_error_and_patch_ownership(telemetry, client):
    from botocore.client import BaseClient

    original = vars(BaseClient)["_make_api_call"]
    exporter = enable(telemetry, "bedrock")
    with Stubber(client) as stub:
        stub.add_client_error(
            "converse",
            service_error_code="ValidationException",
            service_message="private",
            expected_params=params(),
        )
        with pytest.raises(ClientError):
            client.converse(**params())
    attrs = spans(exporter)[0].attributes
    assert "error.type" in attrs
    assert "private" not in str(attrs)
    ct.shutdown()
    assert vars(BaseClient)["_make_api_call"] is original


def test_unrelated_aws_calls_untouched(telemetry, client):
    exporter = enable(telemetry, "bedrock")
    with Stubber(client) as stub:
        expected = {"body": b"payload", "contentType": "application/json"}
        stub.add_response("invoke_model", expected, {"modelId": "test", "body": "{}"})
        assert client.invoke_model(modelId="test", body="{}") is expected
    with (
        closing(
            boto3.client(
                "s3",
                region_name="us-east-1",
                aws_access_key_id="test",
                aws_secret_access_key="test",
            )
        ) as s3,
        Stubber(s3) as stub,
    ):
        stub.add_response("list_buckets", {"Buckets": []}, {})
        assert s3.list_buckets()["Buckets"] == []
    assert spans(exporter) == ()


def test_disabled_bedrock_is_not_patched(monkeypatch):
    from botocore.client import BaseClient

    ct.shutdown()
    original = vars(BaseClient)["_make_api_call"]
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert not ct.init().active
    assert vars(BaseClient)["_make_api_call"] is original


def test_shared_bedrock_vector(telemetry):

    _, exporter = telemetry
    vector = json.loads((ROOT / "spec/bedrock-vectors.json").read_text())
    op = Operation(
        "chat test",
        attributes={
            ai.GEN_AI_PROVIDER_NAME: "aws.bedrock",
            ai.GEN_AI_OPERATION_NAME: "chat",
        },
    )
    request(op, vector["request"])
    response(op, vector["response"])
    op.end()
    attrs = spans(exporter)[0].attributes
    for key, value in vector["expected_attributes"].items():
        assert attrs[key] == (tuple(value) if type(value) is list else value)
    assert json.loads(attrs[ai.GEN_AI_INPUT_MESSAGES]) == vector["expected_input"]
    assert json.loads(attrs[ai.GEN_AI_OUTPUT_MESSAGES]) == vector["expected_output"]
    assert "binary-secret" not in str(attrs)


@pytest.mark.asyncio
async def test_boto3_thread_offload_preserves_context(telemetry, client):
    import asyncio

    exporter = enable(telemetry, "bedrock")
    with Stubber(client) as stub:
        stub.add_response("converse", result(), params())
        with ct.span("async-app") as parent:
            actual = await asyncio.to_thread(client.converse, **params())
            assert actual["output"]["message"]["content"][0]["text"] == "hello back"
    child, _ = spans(exporter)
    assert child.parent == parent.get_span_context()


@pytest.mark.parametrize("policy", ["disabled", "redacted"])
def test_bedrock_content_policy(telemetry, client, policy):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    provider, _ = telemetry
    ct.shutdown()
    exporter = InMemorySpanExporter()
    options = (
        {"capture_content": False}
        if policy == "disabled"
        else {"redact": lambda _: "redacted"}
    )
    ct.init(
        tracer_provider=provider,
        exporter=exporter,
        instrumentations=("bedrock",),
        **options,
    )
    with Stubber(client) as stub:
        stub.add_response("converse", result(), params())
        client.converse(**params())
    attrs = spans(exporter)[0].attributes
    assert "hello" not in str(attrs)
    assert ai.GEN_AI_INPUT_MESSAGES not in attrs
    assert ai.GEN_AI_OUTPUT_MESSAGES not in attrs
    assert attrs[ai.GEN_AI_USAGE_OUTPUT_TOKENS] == 2
