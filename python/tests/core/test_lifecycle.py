import asyncio
import logging
import multiprocessing
import os
import time

import pytest
from conftest import spans
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import confident_trace as ct
from confident_trace._core import runtime


class BrokenExporter(SpanExporter):
    def export(self, spans):
        raise RuntimeError("outage")

    def shutdown(self):
        time.sleep(0.1)


def private_init(exporter=None):
    return ct.init(
        tracer_provider=TracerProvider(shutdown_on_exit=False),
        exporter=exporter or InMemorySpanExporter(),
        instrumentations=(),
    )


def test_export_failure_and_shutdown_budget():
    ct.shutdown()
    ct.init(
        tracer_provider=TracerProvider(shutdown_on_exit=False),
        exporter=BrokenExporter(),
        instrumentations=(),
    )

    @ct.span
    def app():
        return 42

    assert app() == 42
    ct.flush(1000)
    start = time.monotonic()
    assert ct.shutdown(1) is False
    assert time.monotonic() - start < 0.1


@pytest.mark.asyncio
async def test_cancel_is_preserved(telemetry):
    _, exporter = telemetry
    started = asyncio.Event()

    @ct.span
    async def app():
        started.set()
        await asyncio.sleep(10)

    task = asyncio.create_task(app())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert spans(exporter)[0].attributes["error.type"] == "CancelledError"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX only")
def test_fork_exporter_worker_recovers(telemetry):
    _, exporter = telemetry
    ctx = multiprocessing.get_context("fork")
    receive, send = ctx.Pipe(False)

    def child():
        with ct.span("child"):
            pass
        send.send(ct.flush(1000))
        ct.shutdown(1000)
        send.close()

    process = ctx.Process(target=child)
    process.start()
    assert receive.poll(5)
    assert receive.recv() is True
    process.join(5)
    assert process.exitcode == 0
    with ct.span("parent"):
        pass
    assert len(spans(exporter)) == 1


def test_private_provider_also_exports_global_spans(monkeypatch):
    ct.shutdown()
    installed = TracerProvider(shutdown_on_exit=False)
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: installed)
    exporter = InMemorySpanExporter()
    rt = private_init(exporter)
    assert rt.provider is not installed
    with installed.get_tracer("fastapi").start_as_current_span("POST /stream"):
        with ct.span("agent", type="agent"):
            with installed.get_tracer("langchain").start_as_current_span(
                "ChatOpenAI", attributes={"gen_ai.request.model": "m"}
            ):
                pass
    result = {s.name: s for s in spans(exporter)}
    assert set(result) == {"POST /stream", "agent", "ChatOpenAI"}
    assert result["ChatOpenAI"].parent.span_id == result["agent"].context.span_id
    assert result["agent"].parent.span_id == result["POST /stream"].context.span_id
    assert result["POST /stream"].parent is None
    ct.shutdown()


def test_private_provider_becomes_global_when_none_is_set(monkeypatch):
    ct.shutdown()
    installed = [trace.ProxyTracerProvider()]
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: installed[0])
    monkeypatch.setattr(
        trace, "set_tracer_provider", lambda p: installed.__setitem__(0, p)
    )
    provider = TracerProvider(shutdown_on_exit=False)
    rt = ct.init(
        tracer_provider=provider, exporter=InMemorySpanExporter(), instrumentations=()
    )
    assert installed[0] is provider
    assert rt.provider is provider
    ct.shutdown()


def test_global_provider_is_attached_once(monkeypatch):
    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    before = len(provider._active_span_processor._span_processors)
    rt = ct.init(
        tracer_provider=provider, exporter=InMemorySpanExporter(), instrumentations=()
    )
    assert rt.provider is provider
    assert len(provider._active_span_processor._span_processors) == before + 1
    ct.shutdown()


def test_generic_grpc_protocol_without_endpoint_keeps_http_default(monkeypatch):
    ct.shutdown()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.delenv("CONFIDENT_OTEL_ENDPOINT", raising=False)
    rt = ct.init(
        tracer_provider=TracerProvider(shutdown_on_exit=False), instrumentations=()
    )
    assert rt.active
    assert (
        rt.processor.delegate.span_exporter._endpoint
        == "https://otel.confident-ai.com/v1/traces"
    )
    ct.shutdown()


def test_explicit_grpc_without_endpoint_disables_tracing(monkeypatch, caplog):
    ct.shutdown()
    monkeypatch.delenv("CONFIDENT_OTEL_ENDPOINT", raising=False)
    with caplog.at_level(logging.WARNING, logger=runtime.log.name):
        rt = ct.init(
            protocol="grpc",
            tracer_provider=TracerProvider(shutdown_on_exit=False),
            instrumentations=(),
        )
    assert not rt.active
    assert "tracing is disabled" in caplog.text
    ct.shutdown()


def test_invalid_provider_is_not_registered_globally(monkeypatch):
    ct.shutdown()
    installed = [trace.ProxyTracerProvider()]
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: installed[0])
    monkeypatch.setattr(
        trace, "set_tracer_provider", lambda p: installed.__setitem__(0, p)
    )
    rt = ct.init(tracer_provider=trace.NoOpTracerProvider(), instrumentations=())
    assert not rt.active
    assert isinstance(installed[0], trace.ProxyTracerProvider)
    ct.shutdown()


def test_grpc_configuration(monkeypatch):
    ct.shutdown()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv("CONFIDENT_OTEL_ENDPOINT", "http://localhost:4317")
    rt = ct.init(
        tracer_provider=TracerProvider(shutdown_on_exit=False), instrumentations=()
    )
    assert rt.active
    assert rt.processor.delegate.span_exporter._endpoint == "localhost:4317"
    ct.shutdown()
