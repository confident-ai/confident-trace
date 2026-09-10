"""Real LiteLLM SDK, with mock completions and local OpenAI transports."""

import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import httpx
import pytest
from conftest import spans
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct

litellm = pytest.importorskip("litellm")


@pytest.fixture
def captured(telemetry):
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=telemetry[0],
        exporter=exporter,
        instrumentations=("litellm", "openai"),
    )
    yield exporter


@pytest.mark.parametrize("router", [False, True])
@pytest.mark.parametrize("stream", [False, True])
def test_completion(captured, router, stream):
    client = (
        litellm.Router(
            model_list=[
                {
                    "model_name": "alias",
                    "litellm_params": {
                        "model": "openai/gpt-4o-mini",
                        "mock_response": "hello",
                        "api_key": "test",
                    },
                }
            ]
        )
        if router
        else litellm
    )
    with ct.span("parent"):
        result = client.completion(
            "alias" if router else "openai/gpt-4o-mini",
            [{"role": "user", "content": "hi"}],
            mock_response="hello",
            stream=stream,
        )
        if stream:
            assert "".join(c.choices[0].delta.content or "" for c in result) == "hello"
        else:
            assert result.choices[0].message.content == "hello"
    records = spans(captured)
    llm = [s for s in records if s.attributes.get("confident.span.type") == "llm"]
    assert len(llm) == 1
    s = llm[0]
    assert s.attributes["confident.span.integration"] == "LiteLLM"
    assert s.attributes["gen_ai.provider.name"] == "litellm"
    assert s.attributes["gen_ai.request.model"] == (
        "alias" if router else "openai/gpt-4o-mini"
    )
    assert "hello" in s.attributes["gen_ai.output.messages"]
    assert (
        s.parent.span_id
        == next(s for s in records if s.name == "parent").context.span_id
    )


@pytest.mark.parametrize("router", [False, True])
@pytest.mark.parametrize("stream", [False, True])
async def test_async_completion(captured, router, stream):
    client = (
        litellm.Router(
            model_list=[
                {
                    "model_name": "alias",
                    "litellm_params": {
                        "model": "openai/gpt-4o-mini",
                        "mock_response": "hello",
                        "api_key": "test",
                    },
                }
            ]
        )
        if router
        else litellm
    )
    result = await client.acompletion(
        "alias" if router else "openai/gpt-4o-mini",
        [{"role": "user", "content": "hi"}],
        mock_response="hello",
        stream=stream,
    )
    if stream:
        assert (
            "".join([c.choices[0].delta.content or "" async for c in result]) == "hello"
        )
    else:
        assert result.choices[0].message.content == "hello"
    records = spans(captured)
    assert len(records) == 1
    assert records[0].attributes["confident.span.integration"] == "LiteLLM"
    assert "hello" in records[0].attributes["gen_ai.output.messages"]


def test_error(captured):
    with pytest.raises(litellm.RateLimitError):
        litellm.completion(
            model="openai/gpt-4o-mini",
            messages=[],
            mock_response="litellm.RateLimitError",
        )
    records = spans(captured)
    assert len(records) == 1
    assert records[0].status.status_code.name == "ERROR"


def test_restore(captured):
    import wrapt

    assert isinstance(litellm.completion, wrapt.FunctionWrapper)
    ct.shutdown()
    assert not isinstance(litellm.completion, wrapt.FunctionWrapper)
    assert not isinstance(litellm.Router.completion, wrapt.FunctionWrapper)


@pytest.mark.parametrize("matched", [True, False])
def test_proxy(telemetry, matched):
    from openai import OpenAI

    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=telemetry[0],
        exporter=exporter,
        instrumentations=("openai",),
        litellm_proxy_urls=("http://gateway.test/v1/",),
    )
    body = {
        "id": "r1",
        "model": "actual-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
    }
    with OpenAI(
        api_key="test",
        base_url="http://gateway.test/v1" if matched else "http://gateway.test/other",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    ) as client:
        client.chat.completions.create(model="alias", messages=[])
    s = spans(exporter)[0]
    assert s.attributes.get("confident.gateway.name") == (
        "litellm" if matched else None
    )
    assert s.attributes["gen_ai.provider.name"] == ("litellm" if matched else "openai")
    assert s.attributes["confident.span.integration"] == "OpenAI"
    assert s.attributes["gen_ai.request.model"] == "alias"
    assert s.attributes["gen_ai.response.model"] == "actual-model"
    assert s.attributes["gen_ai.usage.input_tokens"] == 4


def test_native_openai_child_suppressed(captured, monkeypatch):
    from openai import OpenAI

    body = {
        "id": "r1",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "hello",
                    "tool_calls": [
                        {
                            "id": "call1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
    }
    with OpenAI(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    ) as client:
        result = litellm.completion(
            model="openai/gpt-4o-mini", messages=[], client=client
        )
    assert result.choices[0].message.tool_calls[0].function.name == "lookup"
    records = spans(captured)
    assert len(records) == 1
    assert records[0].attributes["gen_ai.usage.input_tokens"] == 4
    assert "lookup" in records[0].attributes["gen_ai.output.messages"]


async def test_async_error(captured):
    with pytest.raises(litellm.RateLimitError):
        await litellm.acompletion(
            model="openai/gpt-4o-mini",
            messages=[],
            mock_response="litellm.RateLimitError",
        )
    assert len(spans(captured)) == 1
    assert spans(captured)[0].status.status_code.name == "ERROR"


def test_content_disabled(telemetry):
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=telemetry[0],
        exporter=exporter,
        instrumentations=("litellm",),
        capture_content=False,
    )
    litellm.completion(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "secret"}],
        mock_response="secret",
    )
    s = spans(exporter)[0]
    assert "gen_ai.input.messages" not in s.attributes
    assert "gen_ai.output.messages" not in s.attributes
    assert "secret" not in str(s.attributes)


def test_stream_error(captured):
    stream = litellm.completion(
        model="openai/gpt-4o-mini",
        messages=[],
        stream=True,
        mock_response="Exception: mock_streaming_error",
    )
    with pytest.raises(Exception):
        list(stream)
    records = spans(captured)
    assert len(records) == 1
    assert records[0].status.status_code.name == "ERROR"


async def test_async_stream_error(captured):
    stream = await litellm.acompletion(
        model="openai/gpt-4o-mini",
        messages=[],
        stream=True,
        mock_response="Exception: mock_streaming_error",
    )
    with pytest.raises(Exception):
        async for _ in stream:
            pass
    records = spans(captured)
    assert len(records) == 1
    assert records[0].status.status_code.name == "ERROR"
