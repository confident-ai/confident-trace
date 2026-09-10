"""Real OpenRouter SDK requests against an in-memory HTTP transport."""

import json

import httpx
import pytest
from conftest import spans
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct

openrouter = pytest.importorskip("openrouter")
BODY = {
    "id": "r1",
    "created": 0,
    "model": "actual/model",
    "object": "chat.completion",
    "system_fingerprint": None,
    "choices": [
        {
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "hello",
                "tool_calls": [
                    {
                        "id": "t1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            },
        }
    ],
    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
}


def wire():
    chunk = {
        **BODY,
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "delta": {
                    "role": "assistant",
                    "content": "hello",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "t1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ],
                },
            }
        ],
    }
    return "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"


def setup(telemetry, **options):
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=telemetry[0],
        exporter=exporter,
        instrumentations=("openrouter", "openai"),
        **options,
    )
    return exporter


def response(stream=False, error=False):
    if error:
        return httpx.Response(400, json={"error": {"code": 400, "message": "secret"}})
    if stream:
        return httpx.Response(
            200, text=wire(), headers={"content-type": "text/event-stream"}
        )
    return httpx.Response(200, json=BODY)


def check(exporter):
    records = spans(exporter)
    assert len(records) == 1
    s = records[0]
    assert s.attributes["confident.span.integration"] == "OpenRouter"
    assert s.attributes["gen_ai.provider.name"] == "openrouter"
    assert s.attributes["gen_ai.request.model"] == "requested/alias"
    assert s.attributes["gen_ai.response.model"] == "actual/model"
    assert s.attributes["gen_ai.usage.input_tokens"] == 4
    assert "hello" in s.attributes["gen_ai.output.messages"]
    assert "lookup" in s.attributes["gen_ai.output.messages"]


@pytest.mark.parametrize("stream", [False, True])
def test_native(telemetry, stream):
    exporter = setup(telemetry)
    with openrouter.OpenRouter(
        api_key="test",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: response(stream))),
    ) as client:
        result = client.chat.send(model="requested/alias", messages=[], stream=stream)
        if stream:
            assert "".join(c.choices[0].delta.content or "" for c in result) == "hello"
        else:
            assert result.choices[0].message.content == "hello"
    check(exporter)


@pytest.mark.parametrize("stream", [False, True])
async def test_native_async(telemetry, stream):
    exporter = setup(telemetry)
    async with openrouter.OpenRouter(
        api_key="test",
        async_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: response(stream))
        ),
    ) as client:
        result = await client.chat.send_async(
            model="requested/alias", messages=[], stream=stream
        )
        if stream:
            assert (
                "".join([c.choices[0].delta.content or "" async for c in result])
                == "hello"
            )
        else:
            assert result.choices[0].message.content == "hello"
    check(exporter)


def test_error_and_restore(telemetry):
    from openrouter.chat import Chat

    original = Chat.send
    exporter = setup(telemetry)
    with openrouter.OpenRouter(
        api_key="test",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: response(error=True))
        ),
    ) as client:
        with pytest.raises(Exception):
            client.chat.send(model="requested/alias", messages=[])
    records = spans(exporter)
    assert len(records) == 1
    assert records[0].status.status_code.name == "ERROR"
    assert "secret" not in str(records[0].attributes)
    ct.shutdown()
    assert Chat.send is original


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("https://openrouter.ai.evil.test/api/v1", "openai"),
        ("https://openrouter.ai/other", "openai"),
        ("http://gateway.test/v1", "openrouter"),
    ],
)
def test_proxy(telemetry, url, expected):
    from openai import OpenAI

    exporter = setup(telemetry, openrouter_proxy_urls=("http://gateway.test/v1",))
    with OpenAI(
        api_key="test",
        base_url=url,
        http_client=httpx.Client(transport=httpx.MockTransport(lambda r: response())),
    ) as client:
        client.chat.completions.create(model="requested/alias", messages=[])
    s = spans(exporter)[0]
    assert s.attributes["confident.span.integration"] == "OpenAI"
    assert s.attributes["gen_ai.provider.name"] == expected
    assert s.attributes.get("confident.gateway.name") == (
        "openrouter" if expected == "openrouter" else None
    )


def test_private(telemetry):
    exporter = setup(telemetry, capture_content=False)
    with openrouter.OpenRouter(
        api_key="test",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: response())),
    ) as client:
        client.chat.send(model="requested/alias", messages=[])
    s = spans(exporter)[0]
    assert "gen_ai.input.messages" not in s.attributes
    assert "gen_ai.output.messages" not in s.attributes


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_stream_error_event(telemetry, asynchronous):
    exporter = setup(telemetry)
    chunk = {
        **BODY,
        "object": "chat.completion.chunk",
        "choices": [],
        "error": {"code": 502, "message": "private upstream error"},
    }
    wire = "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200, text=wire, headers={"content-type": "text/event-stream"}
        )
    )
    with httpx.Client(transport=transport) as sync_client:
        async with httpx.AsyncClient(transport=transport) as async_client:
            client = openrouter.OpenRouter(
                api_key="test", client=sync_client, async_client=async_client
            )
            if asynchronous:
                stream = await client.chat.send_async(
                    model="test", messages=[], stream=True
                )
                async for _ in stream:
                    pass
            else:
                stream = client.chat.send(model="test", messages=[], stream=True)
                list(stream)
    records = spans(exporter)
    assert len(records) == 1
    assert records[0].status.status_code.name == "ERROR"
    assert "private upstream error" not in str(records[0].attributes)


def test_early_stream_exit(telemetry):
    exporter = setup(telemetry)
    with openrouter.OpenRouter(
        api_key="test",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: response(True))),
    ) as client:
        with client.chat.send(model="test", messages=[], stream=True) as stream:
            next(stream)
    assert len(spans(exporter)) == 1
