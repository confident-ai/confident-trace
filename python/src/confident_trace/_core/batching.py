"""Keep every export under the collector's request body limit."""

from __future__ import annotations

from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

MAX_EXPORT_BYTES = 24 * 1024 * 1024
SPAN_OVERHEAD = 1024


def value_size(value):
    if type(value) is str:
        return len(value)
    if type(value) in (list, tuple):
        return sum(value_size(item) for item in value) + 2 * len(value)
    return 8


def span_size(span):
    total = SPAN_OVERHEAD + len(span.name or "")
    for key, value in (span.attributes or {}).items():
        total += len(key) + value_size(value)
    for event in span.events:
        total += SPAN_OVERHEAD + len(event.name or "")
        for key, value in (event.attributes or {}).items():
            total += len(key) + value_size(value)
    return total


def batches(spans, max_bytes):
    """Split spans into runs that each fit the limit, preserving their order.

    A span larger than the limit on its own is still yielded alone: there is
    nothing left to split, which is why media carries a per-attribute budget.
    """
    current, used = [], 0
    for span in spans:
        size = span_size(span)
        if current and used + size > max_bytes:
            yield current
            current, used = [], 0
        current.append(span)
        used += size
    if current:
        yield current


class BoundedSpanExporter(SpanExporter):
    """Export in chunks the collector will accept, rather than one oversized body.

    The OTLP exporter retries only timeouts and 5xx, so a body rejected as too
    large is dropped outright — taking every span batched alongside it, media
    or not. Splitting first keeps one large span from costing the rest.
    """

    def __init__(self, exporter, max_bytes=MAX_EXPORT_BYTES):
        self.exporter = exporter
        self.max_bytes = max_bytes

    def __getattr__(self, name):
        # Splitting changes how much is sent per request, not where or how it
        # is sent, so the wrapped exporter's configuration stays readable.
        return getattr(self.exporter, name)

    def export(self, spans):
        results = [
            self.exporter.export(batch) for batch in batches(spans, self.max_bytes)
        ]
        if all(result is SpanExportResult.SUCCESS for result in results):
            return SpanExportResult.SUCCESS
        return SpanExportResult.FAILURE

    def force_flush(self, timeout_millis=30000):
        return self.exporter.force_flush(timeout_millis)

    def shutdown(self):
        return self.exporter.shutdown()
