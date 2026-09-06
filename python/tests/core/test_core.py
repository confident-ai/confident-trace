import asyncio
import json

import pytest
from conftest import spans
from opentelemetry import trace as otel
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

import confident_trace as ct
from confident_trace._core.content import ContentPolicy


def test_nested_plain_otel_and_updates(telemetry):
    provider, exporter = telemetry

    @ct.span
    def tool(x):
        ct.update_trace(tags=["test"], metadata={"a": 1})
        return x + 1

    with ct.span("request", thread_id="session") as root:
        with provider.get_tracer("third_party").start_as_current_span("plain"):
            assert tool(2) == 3
        carrier = {}
        TraceContextTextMapPropagator().inject(carrier)
        assert TraceContextTextMapPropagator().extract(carrier)
    result = spans(exporter)
    assert len(result) == 3
    assert {s.context.trace_id for s in result} == {root.get_span_context().trace_id}
    row = result[-1]
    assert row.attributes["confident.trace.thread_id"] == "session"
    assert row.attributes["confident.trace.tags"] == ("test",)
    assert "confident.trace.input" not in result[0].attributes


@pytest.mark.asyncio
async def test_async_isolation(telemetry):
    _, exporter = telemetry

    @ct.span
    async def task(i):
        await asyncio.sleep(0)
        ct.update_trace(user_id=str(i))
        return i

    assert await asyncio.gather(task(1), task(2)) == [1, 2]
    result = spans(exporter)
    assert len({s.context.trace_id for s in result}) == 2
    assert {s.attributes["confident.trace.user_id"] for s in result} == {"1", "2"}


def test_generator_send_throw_close_and_context(telemetry):
    _, exporter = telemetry

    @ct.span
    def values():
        x = yield 1
        try:
            yield x
        except ValueError:
            yield 3
        return 9

    gen = values()
    assert next(gen) == 1
    assert not otel.get_current_span().get_span_context().is_valid
    assert gen.send(2) == 2
    assert gen.throw(ValueError()) == 3
    with pytest.raises(StopIteration) as done:
        next(gen)
    assert done.value.value == 9
    assert len(spans(exporter)) == 1
    gen = values()
    next(gen)
    gen.close()
    assert len(spans(exporter)) == 2


@pytest.mark.asyncio
async def test_async_generator_close(telemetry):
    _, exporter = telemetry

    @ct.span
    async def values():
        yield 1
        yield 2

    stream = values()
    assert await stream.__anext__() == 1
    assert not otel.get_current_span().get_span_context().is_valid
    await stream.aclose()
    assert len(spans(exporter)) == 1


def test_exception_identity_and_no_message_leak(telemetry):
    _, exporter = telemetry
    error = ValueError("secret")

    @ct.span
    def fail():
        raise error

    with pytest.raises(ValueError) as caught:
        fail()
    assert caught.value is error
    result = spans(exporter)[0]
    assert result.status.status_code == StatusCode.ERROR
    assert "secret" not in str(result.attributes)
    assert not result.events


def test_content_policy():
    class Hostile:
        def __repr__(self):
            raise AssertionError("must not execute")

    assert json.loads(ContentPolicy().encode(Hostile())) == "[unsupported]"
    cyclic = []
    cyclic.append(cyclic)
    assert len(ContentPolicy(max_bytes=128).encode(cyclic)) <= 128
    assert ContentPolicy(redact=lambda x: 1 / 0).encode("secret") is None
    assert ContentPolicy(enabled=False).encode("secret") is None


def test_idempotence_and_shared_provider_ownership(telemetry):
    provider, exporter = telemetry
    assert ct.init().provider is provider
    ct.shutdown()
    # Our shutdown must not shut down the provider or other exporters.
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    other = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(other))
    with provider.get_tracer("other").start_as_current_span("still alive"):
        pass
    assert len(other.get_finished_spans()) == 1


def test_disabled_no_exporter_or_patching(monkeypatch):
    ct.shutdown()
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    result = ct.init(endpoint="bad", max_content_bytes=-1)
    assert not result.active

    @ct.span
    def f():
        return 1

    assert f() == 1


def test_configuration(monkeypatch):
    ct.shutdown()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/base/")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://localhost:9999/specific"
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "generic=one")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "specific=two")
    monkeypatch.setenv("CONFIDENT_API_KEY", "test-key")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "2")
    rt = ct.init(
        tracer_provider=TracerProvider(shutdown_on_exit=False), instrumentations=()
    )
    exporter = rt.processor.delegate.span_exporter
    assert exporter._endpoint == "http://localhost:9999/specific"
    assert exporter._timeout == 2
    assert exporter._headers == {"specific": "two", "x-confident-api-key": "test-key"}
    ct.shutdown()


def test_thread_id_groups_traces_without_changing_parentage(telemetry):
    _, exporter = telemetry
    with ct.span("request", thread_id="s") as first:
        with ct.span("tool") as child:
            assert (
                child.get_span_context().trace_id == first.get_span_context().trace_id
            )
    with ct.span("request", thread_id="s") as second:
        pass
    result = spans(exporter)
    assert first.get_span_context().trace_id != second.get_span_context().trace_id
    roots = [s for s in result if s.parent is None]
    assert len(roots) == 2
    assert all(s.attributes["confident.trace.thread_id"] == "s" for s in roots)
    assert not hasattr(ct, "turn")


def test_resource_env_and_explicit_exporter_precedence(monkeypatch):
    ct.shutdown()
    from opentelemetry import trace as api
    from opentelemetry.exporter.otlp.proto.http import Compression

    holder = [api.ProxyTracerProvider()]
    monkeypatch.setattr(api, "get_tracer_provider", lambda: holder[0])
    monkeypatch.setattr(
        api, "set_tracer_provider", lambda value: holder.__setitem__(0, value)
    )
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES",
        "service.name=env-service,confident.trace.environment=staging",
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/base/")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_COMPRESSION", "gzip")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE", "/tmp/test-ca.pem")
    rt = ct.init(resource_attributes={"service.name": "explicit"}, instrumentations=())
    exporter = rt.processor.delegate.span_exporter
    assert rt.provider.resource.attributes["service.name"] == "explicit"
    assert rt.provider.resource.attributes["confident.trace.environment"] == "staging"
    assert exporter._endpoint == "http://localhost:4318/base/v1/traces"
    assert exporter._compression == Compression.Gzip
    assert exporter._certificate_file == "/tmp/test-ca.pem"
    ct.shutdown()
    rt = ct.init(
        endpoint="http://localhost:5555/exact",
        timeout=1,
        compression="none",
        instrumentations=(),
    )
    assert (
        rt.processor.delegate.span_exporter._endpoint == "http://localhost:5555/exact"
    )
    assert rt.processor.delegate.span_exporter._timeout == 1
    ct.shutdown()


def test_redaction_and_capture_opt_out(telemetry):
    provider, _ = telemetry
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=provider,
        exporter=exporter,
        instrumentations=(),
        redact=lambda _: "REDACTED",
    )

    @ct.span
    def app(secret):
        return secret

    assert app("secret") == "secret"
    result = spans(exporter)[0]
    assert "secret" not in str(result.attributes)
    assert "REDACTED" in result.attributes["confident.span.input"]
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=provider,
        exporter=exporter,
        instrumentations=(),
        capture_content=False,
    )
    app("secret")
    result = spans(exporter)[0]
    assert "confident.span.input" not in result.attributes
    assert "confident.span.output" not in result.attributes


def test_disabled_scopes_preserve_external_context(telemetry, monkeypatch):
    provider, _ = telemetry
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    with provider.get_tracer("external").start_as_current_span("parent") as parent:
        with ct.span("ignored", thread_id="ignored"):
            ct.update_trace(user_id="ignored")
            assert otel.get_current_span() is parent
        assert "confident.trace.user_id" not in parent.attributes


@pytest.mark.parametrize(
    "decorator",
    [ct.span(), ct.span(name="named"), ct.span("named"), ct.span(kind="tool")],
)
def test_configured_span_decorator(telemetry, decorator):
    _, exporter = telemetry

    @decorator
    def work(value):
        return value + 1

    assert work(2) == 3
    result = spans(exporter)
    assert len(result) == 1
    assert result[0].name in (
        "named",
        work.__qualname__,
        "execute_tool " + work.__qualname__,
    )
    assert not hasattr(ct, "trace")


def test_named_span_context_and_decorator_share_parent(telemetry):
    _, exporter = telemetry

    @ct.span(name="child", attributes={"custom": "value"})
    async def child():
        return 7

    async def run():
        with ct.span(name="parent") as parent:
            assert await child() == 7
            return parent.get_span_context()

    parent = asyncio.run(run())
    result = spans(exporter)
    assert result[0].parent == parent
    assert result[0].attributes["custom"] == "value"


def test_explicit_config_overrides_trace_and_generic_env(monkeypatch):
    ct.shutdown()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "generic=one")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS", "trace=two,x-confident-api-key=env-header"
    )
    monkeypatch.setenv("CONFIDENT_API_KEY", "env-key")
    rt = ct.init(
        tracer_provider=TracerProvider(shutdown_on_exit=False),
        instrumentations=(),
        endpoint="http://localhost:9999/custom",
        api_key="explicit-key",
        headers={"trace": "explicit-header"},
    )
    exporter = rt.processor.delegate.span_exporter
    assert exporter._endpoint == "http://localhost:9999/custom"
    assert exporter._headers == {
        "trace": "explicit-header",
        "x-confident-api-key": "explicit-key",
    }
    ct.shutdown()
    rt = ct.init(
        tracer_provider=TracerProvider(shutdown_on_exit=False),
        instrumentations=(),
        protocol="grpc",
        endpoint="http://localhost:4317",
    )
    assert rt.active
    assert rt.processor.delegate.span_exporter._endpoint == "localhost:4317"
    ct.shutdown()
