import json

import httpx
import pytest
from conftest import spans

import confident_trace as ct


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


def test_openai_chat(telemetry):
    from openai import OpenAI

    exporter = enable(telemetry, "openai")
    body = {
        "id": "chat-1",
        "object": "chat.completion",
        "model": "gpt-test",
        "created": 0,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }
    client = OpenAI(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    )
    result = client.chat.completions.create(
        model="gpt-test", messages=[{"role": "user", "content": "hi"}]
    )
    assert result.choices[0].message.content == "hello"
    captured = spans(exporter)
    assert len(captured) == 1
    assert captured[0].attributes["gen_ai.usage.input_tokens"] == 2
    assert (
        json.loads(captured[0].attributes["gen_ai.output.messages"])[0]["parts"][0][
            "content"
        ]
        == "hello"
    )
    client.close()


@pytest.mark.asyncio
async def test_openai_async(telemetry):
    from openai import AsyncOpenAI

    exporter = enable(telemetry, "openai")
    body = {
        "id": "r1",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "gpt-test",
        "output": [
            {
                "type": "message",
                "id": "m1",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "hello", "annotations": []}
                ],
            }
        ],
        "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
    }
    async with AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    ) as client:
        result = await client.responses.create(model="gpt-test", input="hi")
    assert result.id == "r1"
    captured = spans(exporter)
    assert len(captured) == 1
    assert captured[0].attributes["gen_ai.usage.output_tokens"] == 1


def test_openai_stream(telemetry):
    from openai import OpenAI

    exporter = enable(telemetry, "openai")
    chunk = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-test",
        "choices": [{"index": 0, "delta": {"content": "hello"}, "finish_reason": None}],
    }
    wire = "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
    with OpenAI(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, text=wire, headers={"content-type": "text/event-stream"}
                )
            )
        ),
    ) as client:
        stream = client.chat.completions.create(
            model="gpt-test", messages=[], stream=True
        )
        assert [c.choices[0].delta.content for c in stream] == ["hello"]
        stream.close()
    captured = spans(exporter)
    assert len(captured) == 1
    assert "hello" in captured[0].attributes["gen_ai.output.messages"]


def test_anthropic(telemetry):
    from anthropic import Anthropic, _base_client

    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    exporter = enable(telemetry, "anthropic")
    body = {
        "id": "m1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }
    with Anthropic(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    ) as client:
        result = client.messages.create(model="claude-test", messages=[], max_tokens=5)
    assert result.content[0].text == "hello"
    assert spans(exporter)[0].attributes["gen_ai.provider.name"] == "anthropic"


def test_google_genai(telemetry):
    from google import genai
    from google.genai import types

    exporter = enable(telemetry, "google_genai")
    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "hello"}]}}],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
        "modelVersion": "gemini-test",
    }
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    with genai.Client(
        api_key="test",
        http_options=types.HttpOptions(client_args={"transport": transport}),
    ) as client:
        result = client.models.generate_content(model="gemini-test", contents="hi")
    assert result.text == "hello"
    assert spans(exporter)[0].attributes["gen_ai.usage.input_tokens"] == 2


def anthropic_wire():
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "m1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-test",
                    "content": [],
                    "usage": {"input_tokens": 2, "output_tokens": 0},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "hello"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(
        f"event: {name}\ndata: {json.dumps(body)}\n\n" for name, body in events
    )


def test_anthropic_stream_helper(telemetry):
    from anthropic import Anthropic, _base_client

    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    exporter = enable(telemetry, "anthropic")
    with Anthropic(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    text=anthropic_wire(),
                    headers={"content-type": "text/event-stream"},
                )
            )
        ),
    ) as client:
        with client.messages.stream(
            model="claude-test", messages=[], max_tokens=5
        ) as stream:
            assert list(stream.text_stream) == ["hello"]
            assert stream.get_final_message().content[0].text == "hello"
    captured = spans(exporter)
    assert len(captured) == 1
    assert captured[0].attributes["gen_ai.usage.output_tokens"] == 1
    assert "hello" in captured[0].attributes["gen_ai.output.messages"]


@pytest.mark.asyncio
async def test_anthropic_async_stream_helper(telemetry):
    from anthropic import AsyncAnthropic, _base_client

    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    exporter = enable(telemetry, "anthropic")
    async with AsyncAnthropic(
        api_key="test",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200,
                    text=anthropic_wire(),
                    headers={"content-type": "text/event-stream"},
                )
            )
        ),
    ) as client:
        async with client.messages.stream(
            model="claude-test", messages=[], max_tokens=5
        ) as stream:
            assert [text async for text in stream.text_stream] == ["hello"]
    assert len(spans(exporter)) == 1


@pytest.mark.asyncio
async def test_google_async_stream(telemetry):
    from google import genai
    from google.genai import types

    exporter = enable(telemetry, "google_genai")
    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "hello"}]}}],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
    }
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            text="data: " + json.dumps(body) + "\n\n",
            headers={"content-type": "text/event-stream"},
        )
    )
    async with genai.Client(
        api_key="test",
        http_options=types.HttpOptions(async_client_args={"transport": transport}),
    ).aio as client:
        stream = await client.models.generate_content_stream(
            model="gemini-test", contents="hi"
        )
        assert [chunk.text async for chunk in stream] == ["hello"]
    assert len(spans(exporter)) == 1


def test_provider_exception_is_not_retried(telemetry):
    from openai import BadRequestError, OpenAI

    exporter = enable(telemetry, "openai")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            400, json={"error": {"message": "secret", "type": "bad_request"}}
        )

    with OpenAI(
        api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handle))
    ) as client:
        with pytest.raises(BadRequestError):
            client.chat.completions.create(model="test", messages=[])
    assert len(requests) == 1
    result = spans(exporter)[0]
    assert result.attributes["error.type"] == "BadRequestError"
    assert "secret" not in str(result.attributes)


def test_instrumentation_restores_methods(telemetry):
    from openai.resources.chat.completions import Completions

    original = vars(Completions)["create"]
    enable(telemetry, "openai")
    assert vars(Completions)["create"] is not original
    ct.shutdown()
    assert vars(Completions)["create"] is original


@pytest.mark.parametrize("ending", ["exhaust", "close", "error"])
def test_openai_stream_lifecycle_with_transport(telemetry, ending):
    from openai import OpenAI

    exporter = enable(telemetry, "openai")
    error = httpx.ReadError("private error detail")

    class Body(httpx.SyncByteStream):
        closed = False
        advanced = 0

        def __iter__(self):
            self.advanced += 1
            yield b'data: {"id":"c1","object":"chat.completion.chunk","created":0,"model":"test","choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
            self.advanced += 1
            if ending == "error":
                raise error
            yield b"data: [DONE]\n\n"

        def close(self):
            self.closed = True

    body = Body()
    with OpenAI(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, stream=body, headers={"content-type": "text/event-stream"}
                )
            )
        ),
    ) as client:
        stream = client.chat.completions.create(model="test", messages=[], stream=True)
        assert next(stream).choices[0].delta.content == "partial"
        assert body.advanced == 1  # Instrumentation never consumes ahead.
        if ending == "error":
            with pytest.raises(httpx.ReadError) as caught:
                next(stream)
            assert caught.value is error
        elif ending == "exhaust":
            assert list(stream) == []
        stream.close()
    assert body.closed
    captured = spans(exporter)
    assert len(captured) == 1
    assert "partial" in captured[0].attributes["gen_ai.output.messages"]
    assert "private error detail" not in str(captured[0].attributes)
    if ending == "error":
        assert captured[0].attributes["error.type"] == "ReadError"


@pytest.mark.asyncio
async def test_openai_stream_cancellation_preserves_partial_content(telemetry):
    import asyncio

    from openai import AsyncOpenAI

    exporter = enable(telemetry, "openai")
    waiting = asyncio.Event()

    class Body(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'data: {"id":"c1","object":"chat.completion.chunk","created":0,"model":"test","choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
            waiting.set()
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    body = Body()
    async with AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, stream=body, headers={"content-type": "text/event-stream"}
                )
            )
        ),
    ) as client:
        stream = await client.chat.completions.create(
            model="test", messages=[], stream=True
        )
        assert (await stream.__anext__()).choices[0].delta.content == "partial"
        task = asyncio.create_task(stream.__anext__())
        await asyncio.wait_for(waiting.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await stream.close()
    assert body.closed
    captured = spans(exporter)
    assert len(captured) == 1
    assert captured[0].attributes["error.type"] == "CancelledError"
    assert (
        json.loads(captured[0].attributes["gen_ai.output.messages"])[0]["finish_reason"]
        == ""
    )


@pytest.mark.asyncio
async def test_anthropic_async_create_stream(telemetry):
    from anthropic import AsyncAnthropic, _base_client

    transport_api = getattr(_base_client, "httpx2", None) or _base_client.httpx
    exporter = enable(telemetry, "anthropic")
    async with AsyncAnthropic(
        api_key="test",
        http_client=transport_api.AsyncClient(
            transport=transport_api.MockTransport(
                lambda r: transport_api.Response(
                    200,
                    text=anthropic_wire(),
                    headers={"content-type": "text/event-stream"},
                )
            )
        ),
    ) as client:
        stream = await client.messages.create(
            model="claude-test", messages=[], max_tokens=5, stream=True
        )
        events = [event async for event in stream]
        assert events[-1].type == "message_stop"
        await stream.close()
    captured = spans(exporter)
    assert len(captured) == 1
    assert captured[0].attributes["gen_ai.usage.output_tokens"] == 1
    output = json.loads(captured[0].attributes["gen_ai.output.messages"])
    assert output[0]["parts"][0]["content"] == "hello"
    assert output[0]["finish_reason"] == "stop"
