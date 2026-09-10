"""Real provider SDKs routed to Bifrost-compatible mock HTTP transports."""

import httpx
import pytest
from conftest import spans
from gateway_fixtures import anthropic_wire
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct


def enable(telemetry):
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=telemetry[0],
        exporter=exporter,
        instrumentations=("openai", "anthropic"),
        bifrost_proxy_urls=(
            "http://bifrost.test/openai",
            "http://bifrost.test/anthropic",
        ),
        capture_content=False,
    )
    return exporter


def verify(exporter, integration):
    captured = spans(exporter)
    assert len(captured) == 1
    attrs = captured[0].attributes
    assert attrs["confident.gateway.name"] == "bifrost"
    assert attrs["gen_ai.provider.name"] == "bifrost"
    assert attrs["confident.span.integration"] == integration
    assert "gen_ai.output.messages" not in attrs
    assert "secret" not in str(attrs)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_anthropic_stream(telemetry, asynchronous):
    from anthropic import Anthropic, AsyncAnthropic, _base_client

    wire = getattr(_base_client, "httpx2", None) or _base_client.httpx
    exporter = enable(telemetry)

    def respond(request):
        assert request.url.path == "/anthropic/v1/messages"
        assert request.headers["x-bf-vk"] == "secret"
        return wire.Response(
            200, text=anthropic_wire(), headers={"content-type": "text/event-stream"}
        )

    kwargs = dict(
        api_key="secret",
        base_url="http://bifrost.test/anthropic",
        default_headers={"x-bf-vk": "secret"},
    )
    args = dict(model="alias", max_tokens=8, messages=[])
    if asynchronous:
        async with AsyncAnthropic(
            **kwargs,
            http_client=wire.AsyncClient(transport=wire.MockTransport(respond)),
        ) as client:
            async with client.messages.stream(**args) as stream:
                assert "".join([text async for text in stream.text_stream]) == "hello"
    else:
        with Anthropic(
            **kwargs, http_client=wire.Client(transport=wire.MockTransport(respond))
        ) as client:
            with client.messages.stream(**args) as stream:
                assert "".join(stream.text_stream) == "hello"
    verify(exporter, "Anthropic")


@pytest.mark.parametrize("api", ["chat", "responses"])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_openai(telemetry, api, asynchronous):
    from openai import AsyncOpenAI, OpenAI

    exporter = enable(telemetry)
    body = dict(
        id="r1",
        model="actual",
        choices=[],
        output=[],
        usage={
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "input_tokens": 2,
            "output_tokens": 1,
        },
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    kwargs = dict(api_key="secret", base_url="http://bifrost.test/openai/")
    args = dict(
        model="alias", **({"messages": []} if api == "chat" else {"input": "hi"})
    )
    if asynchronous:
        async with AsyncOpenAI(
            **kwargs, http_client=httpx.AsyncClient(transport=transport)
        ) as client:
            resource = client.chat.completions if api == "chat" else client.responses
            await resource.create(**args)
    else:
        with OpenAI(**kwargs, http_client=httpx.Client(transport=transport)) as client:
            resource = client.chat.completions if api == "chat" else client.responses
            resource.create(**args)
    verify(exporter, "OpenAI")


@pytest.mark.parametrize(
    "url",
    [
        "http://bifrost.test/openai-other",
        "https://bifrost.test/openai",
        "http://bifrost.test:8080/openai",
        "http://bifrost.test/",
    ],
)
def test_exact_endpoint(url):
    from openai import OpenAI

    from confident_trace.integrations._shared.gateways import gateway_name

    with OpenAI(api_key="secret", base_url=url) as client:
        assert (
            gateway_name(
                client.chat.completions, bifrost_urls=("http://bifrost.test/openai",)
            )
            is None
        )
