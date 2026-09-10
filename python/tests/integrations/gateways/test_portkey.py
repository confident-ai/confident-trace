"""Portkey's real SDK with mock HTTP/SSE responses; no gateway credentials."""

import json

import httpx
import pytest
from conftest import spans
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct

portkey = pytest.importorskip("portkey_ai")
CHAT = {
    "id": "chat-1",
    "object": "chat.completion",
    "created": 0,
    "model": "actual-model",
    "choices": [
        {
            "index": 0,
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
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
}
RESPONSE = {
    "id": "r1",
    "object": "response",
    "created_at": 0,
    "status": "completed",
    "model": "actual-model",
    "output": [
        {
            "id": "m1",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hello", "annotations": []}],
        }
    ],
    "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
}


def transport(api="chat", stream=False, error=False, requests=None):
    def handle(request):
        if requests is not None:
            requests.append(request)
        if error:
            return httpx.Response(
                400,
                json={"error": {"message": "private secret", "type": "bad_request"}},
            )
        body = CHAT if api == "chat" else RESPONSE
        if not stream:
            return httpx.Response(200, json=body)
        if api == "chat":
            chunks = [
                {
                    **CHAT,
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": "hello",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        **CHAT["choices"][0]["message"]["tool_calls"][
                                            0
                                        ],
                                    }
                                ],
                            },
                            "finish_reason": "stop",
                        }
                    ],
                }
            ]
        else:
            chunks = [
                {"type": "response.output_text.delta", "delta": "hello"},
                {"type": "response.completed", "response": RESPONSE},
            ]
        wire = (
            "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, text=wire, headers={"content-type": "text/event-stream"}
        )

    return httpx.MockTransport(handle)


def setup(telemetry, **options):
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=telemetry[0],
        exporter=exporter,
        instrumentations=("portkey", "openai"),
        **options,
    )
    return exporter


def verify(exporter, api):
    records = spans(exporter)
    assert len(records) == 1
    a = records[0].attributes
    assert a["confident.span.integration"] == "Portkey"
    assert a["gen_ai.provider.name"] == "portkey"
    assert a["gen_ai.request.model"] == "alias"
    assert a["gen_ai.response.model"] == "actual-model"
    assert a["gen_ai.usage.input_tokens"] == 4
    assert "hello" in a["gen_ai.output.messages"]
    if api == "chat":
        assert "lookup" in a["gen_ai.output.messages"]


@pytest.mark.parametrize("api", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_native(telemetry, api, stream):
    exporter = setup(telemetry)
    requests = []
    with portkey.Portkey(
        api_key="test",
        provider="openai",
        http_client=httpx.Client(transport=transport(api, stream, requests=requests)),
    ) as client:
        client = client.with_options(trace_id="user-trace")
        method = (
            client.chat.completions.create if api == "chat" else client.responses.create
        )
        result = method(
            model="alias",
            stream=stream,
            **({"messages": []} if api == "chat" else {"input": "hi"}),
        )
        if stream:
            assert list(result)
    verify(exporter, api)
    assert len(requests) == 1
    assert requests[0].headers.get("x-portkey-trace-id") == "user-trace"


@pytest.mark.parametrize("api", ["chat", "responses"])
@pytest.mark.parametrize("stream", [False, True])
async def test_native_async(telemetry, api, stream):
    exporter = setup(telemetry)
    async with portkey.AsyncPortkey(
        api_key="test", http_client=httpx.AsyncClient(transport=transport(api, stream))
    ) as client:
        method = (
            client.chat.completions.create if api == "chat" else client.responses.create
        )
        result = await method(
            model="alias",
            stream=stream,
            **({"messages": []} if api == "chat" else {"input": "hi"}),
        )
        if stream:
            assert [chunk async for chunk in result]
    verify(exporter, api)


@pytest.mark.parametrize("api", ["chat", "responses"])
def test_error(telemetry, api):
    exporter = setup(telemetry)
    with portkey.Portkey(
        api_key="test", http_client=httpx.Client(transport=transport(api, error=True))
    ) as client:
        method = (
            client.chat.completions.create if api == "chat" else client.responses.create
        )
        with pytest.raises(Exception):
            method(
                model="alias",
                **({"messages": []} if api == "chat" else {"input": "hi"}),
            )
    records = spans(exporter)
    assert len(records) == 1
    assert records[0].status.status_code.name == "ERROR"
    assert "private secret" not in str(records[0].attributes)


def test_private_and_restore(telemetry):
    from portkey_ai.api_resources.apis.chat_complete import Completions

    original = Completions.create
    exporter = setup(telemetry, capture_content=False)
    with portkey.Portkey(
        api_key="test", http_client=httpx.Client(transport=transport())
    ) as client:
        client.chat.completions.create(model="alias", messages=[])
    assert "gen_ai.output.messages" not in spans(exporter)[0].attributes
    ct.shutdown()
    assert Completions.create is original


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://api.portkey.ai/v1/", "portkey"),
        ("https://api.portkey.ai.evil.test/v1", "openai"),
        ("https://api.portkey.ai/other", "openai"),
        ("http://custom.test/v1", "portkey"),
    ],
)
def test_proxy(telemetry, url, expected):
    from openai import OpenAI

    exporter = setup(telemetry, portkey_proxy_urls=("http://custom.test/v1",))
    with OpenAI(
        api_key="test", base_url=url, http_client=httpx.Client(transport=transport())
    ) as client:
        client.chat.completions.create(model="alias", messages=[])
    attrs = spans(exporter)[0].attributes
    assert attrs["confident.span.integration"] == "OpenAI"
    assert attrs["gen_ai.provider.name"] == expected
    assert attrs.get("confident.gateway.name") == (
        "portkey" if expected == "portkey" else None
    )


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_early_stream_exit(telemetry, asynchronous):
    exporter = setup(telemetry)
    if asynchronous:
        async with portkey.AsyncPortkey(
            api_key="test",
            http_client=httpx.AsyncClient(transport=transport(stream=True)),
        ) as client:
            async with await client.chat.completions.create(
                model="alias", messages=[], stream=True
            ) as stream:
                await stream.__anext__()
    else:
        with portkey.Portkey(
            api_key="test", http_client=httpx.Client(transport=transport(stream=True))
        ) as client:
            with client.chat.completions.create(
                model="alias", messages=[], stream=True
            ) as stream:
                next(stream)
    assert len(spans(exporter)) == 1


async def test_async_error(telemetry):
    exporter = setup(telemetry)
    async with portkey.AsyncPortkey(
        api_key="test", http_client=httpx.AsyncClient(transport=transport(error=True))
    ) as client:
        with pytest.raises(Exception):
            await client.chat.completions.create(model="alias", messages=[])
    records = spans(exporter)
    assert len(records) == 1
    assert records[0].status.status_code.name == "ERROR"
