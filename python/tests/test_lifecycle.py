import asyncio
import multiprocessing
import os
import time

import pytest
from conftest import spans
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter

import confident_trace as ct


class BrokenExporter(SpanExporter):
    def export(self, spans):
        raise RuntimeError("outage")

    def shutdown(self):
        time.sleep(0.1)


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


def test_grpc_configuration(monkeypatch):
    ct.shutdown()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://localhost:4317")
    rt = ct.init(
        tracer_provider=TracerProvider(shutdown_on_exit=False), instrumentations=()
    )
    assert rt.active
    assert rt.processor.delegate.span_exporter._endpoint == "localhost:4317"
    ct.shutdown()
