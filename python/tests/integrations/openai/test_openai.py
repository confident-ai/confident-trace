import json

import httpx
import pytest
from conftest import enable, spans

import confident_trace as ct


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
        assert body.advanced == 1
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
