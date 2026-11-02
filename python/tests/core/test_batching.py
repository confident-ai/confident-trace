"""Splitting keeps one oversized span from costing the spans batched with it."""

import pytest
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from confident_trace._core.batching import (
    BoundedSpanExporter,
    batches,
    span_size,
)


class Recorder:
    """A SpanExporter that remembers the shape of every request it was given."""

    def __init__(self, fail_over=None):
        self.requests = []
        self.fail_over = fail_over
        self.flushed = False
        self.stopped = False
        self._endpoint = "https://collector.invalid/v1/traces"

    def export(self, spans):
        self.requests.append(list(spans))
        if self.fail_over is not None and len(spans) > self.fail_over:
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis=30000):
        self.flushed = True
        return True

    def shutdown(self):
        self.stopped = True


class FakeSpan:
    def __init__(self, name="span", attributes=None):
        self.name = name
        self.attributes = attributes or {}
        self.events = ()


def span_of(payload_bytes):
    return FakeSpan(attributes={"gen_ai.input.messages": "x" * payload_bytes})


def test_size_grows_with_the_payload_it_carries():
    small = span_size(span_of(10))
    large = span_size(span_of(10_000))
    assert large - small == pytest.approx(9_990, abs=64)


def test_a_batch_that_fits_is_sent_as_one_request():
    spans = [span_of(1_000) for _ in range(4)]
    assert list(batches(spans, 1_000_000)) == [spans]


def test_an_oversized_batch_is_split_in_order():
    spans = [span_of(10_000) for _ in range(6)]
    result = list(batches(spans, span_size(span_of(10_000)) * 2))
    assert [len(batch) for batch in result] == [2, 2, 2]
    assert [span for batch in result for span in batch] == spans


def test_a_span_too_large_alone_is_still_sent_alone():
    spans = [span_of(10), span_of(500_000), span_of(10)]
    result = list(batches(spans, 1_000))
    assert [len(batch) for batch in result] == [1, 1, 1]
    assert result[1] == [spans[1]]


def test_the_exporter_splits_what_the_collector_would_reject():
    recorder = Recorder()
    exporter = BoundedSpanExporter(recorder, max_bytes=span_size(span_of(10)) * 2)
    spans = [span_of(10) for _ in range(5)]
    assert exporter.export(spans) is SpanExportResult.SUCCESS
    assert [len(request) for request in recorder.requests] == [2, 2, 1]


def test_one_failed_request_fails_the_export():
    recorder = Recorder(fail_over=1)
    exporter = BoundedSpanExporter(recorder, max_bytes=span_size(span_of(10)) * 2)
    assert exporter.export([span_of(10) for _ in range(3)]) is SpanExportResult.FAILURE
    assert len(recorder.requests) == 2  # Later batches are still attempted.


def test_configuration_stays_readable_through_the_wrapper():
    recorder = Recorder()
    exporter = BoundedSpanExporter(recorder)
    assert exporter._endpoint == recorder._endpoint
    exporter.force_flush()
    exporter.shutdown()
    assert recorder.flushed and recorder.stopped


@pytest.mark.parametrize("payload", ["x" * 1200, "界" * 1200], ids=["ascii", "unicode"])
@pytest.mark.parametrize(
    "field",
    [
        "span_name",
        "attribute_key",
        "attribute_value",
        "array_value",
        "event_name",
        "event_key",
        "event_value",
    ],
)
def test_unicode_batches_fit_the_encoded_otlp_limit(field, payload):
    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({}), shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = provider.get_tracer("batching-regression")
    for _ in range(2):
        with tracer.start_as_current_span(
            payload if field == "span_name" else "span"
        ) as span:
            if field == "attribute_key":
                span.set_attribute(payload, "value")
            elif field == "attribute_value":
                span.set_attribute("value", payload)
            elif field == "array_value":
                span.set_attribute("values", [payload])
            elif field == "event_name":
                span.add_event(payload)
            elif field == "event_key":
                span.add_event("event", {payload: "value"})
            elif field == "event_value":
                span.add_event("event", {"value": payload})
    spans = memory.get_finished_spans()
    provider.shutdown()

    class ByteLimitedRecorder(Recorder):
        def export(self, spans):
            self.requests.append(list(spans))
            body = encode_spans(spans).SerializeToString()
            return (
                SpanExportResult.SUCCESS
                if len(body) <= 7000
                else SpanExportResult.FAILURE
            )

    recorder = ByteLimitedRecorder()
    exporter = BoundedSpanExporter(recorder, max_bytes=7000)
    assert exporter.export(spans) is SpanExportResult.SUCCESS
    assert [span for batch in recorder.requests for span in batch] == list(spans)
    assert all(
        len(encode_spans(batch).SerializeToString()) <= 7000
        for batch in recorder.requests
    )
