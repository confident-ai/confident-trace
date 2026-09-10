"""Real provider SDKs routed to TrueFoundry-compatible mock HTTP transports."""

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
        truefoundry_proxy_urls=("http://truefoundry.test/gateway",),
        capture_content=False,
    )
    return exporter


def verify(exporter, integration):
    captured = spans(exporter)
    assert len(captured) == 1
    attrs = captured[0].attributes
    assert attrs["confident.gateway.name"] == "truefoundry"
    assert attrs["gen_ai.provider.name"] == "truefoundry"
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
        assert request.url.path == "/gateway/v1/messages"
        assert request.headers["authorization"] == "Bearer secret"
        return wire.Response(
            200, text=anthropic_wire(), headers={"content-type": "text/event-stream"}
        )

    kwargs = dict(
        api_key="secret",
        base_url="http://truefoundry.test/gateway",
        default_headers={"Authorization": "Bearer secret"},
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
    kwargs = dict(api_key="secret", base_url="http://truefoundry.test/gateway/")
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
        "http://truefoundry.test/gateway-other",
        "https://truefoundry.test/gateway",
        "http://truefoundry.test:8080/gateway",
        "http://truefoundry.test/",
    ],
)
def test_exact_endpoint(url):
    from openai import OpenAI

    from confident_trace.integrations._shared.gateways import gateway_name

    with OpenAI(api_key="secret", base_url=url) as client:
        assert (
            gateway_name(
                client.chat.completions,
                truefoundry_urls=("http://truefoundry.test/gateway",),
            )
            is None
        )


@pytest.mark.parametrize("sdk", ["openai", "anthropic"])
def test_gateway_errors(telemetry, sdk):
    from anthropic import Anthropic, _base_client
    from openai import OpenAI
    from opentelemetry.trace import StatusCode

    exporter = enable(telemetry)
    wire = (
        httpx
        if sdk == "openai"
        else (getattr(_base_client, "httpx2", None) or _base_client.httpx)
    )

    def respond(request):
        assert request.headers["authorization"] == "Bearer secret"
        return wire.Response(
            400,
            json={"error": {"type": "invalid_request_error", "message": "bad request"}},
        )

    cls = OpenAI if sdk == "openai" else Anthropic
    with cls(
        api_key="secret",
        base_url="http://truefoundry.test/gateway",
        default_headers={"Authorization": "Bearer secret"},
        max_retries=0,
        http_client=wire.Client(transport=wire.MockTransport(respond)),
    ) as client:
        resource = client.chat.completions if sdk == "openai" else client.messages
        with pytest.raises(Exception) as caught:
            resource.create(
                model="alias",
                messages=[],
                **({"max_tokens": 8} if sdk == "anthropic" else {}),
            )
        assert caught.value.status_code == 400
    verify(exporter, "OpenAI" if sdk == "openai" else "Anthropic")
    assert spans(exporter)[0].status.status_code == StatusCode.ERROR
