"""Real SDK callbacks, synthetic time, and separate destination validation."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace._core import ancestry, runtime


def router():
    return runtime.current().processor.delegate


def start(provider, name, parent=None, ai=False, **kwargs):
    return provider.get_tracer("test").start_span(
        name,
        context=trace.set_span_in_context(parent)
        if parent
        else trace.set_span_in_context(trace.INVALID_SPAN),
        attributes={"gen_ai.operation.name": "chat"} if ai else None,
        **kwargs,
    )


def captured(exporter):
    ct.flush()
    return exporter.get_finished_spans()


@pytest.mark.parametrize("late", [False, True])
def test_intermediate_end_before_ai_descendant(telemetry, late):
    provider, exporter = telemetry
    witness = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(witness))
    a = start(provider, "A", ai=True)
    middle = start(provider, "HTTP", a)
    b = start(provider, "B", middle, ai=not late)
    middle.end()
    if late:
        assert len(router().ancestry.pending) == 1
        b.set_attribute("gen_ai.operation.name", "chat")
    b.end()
    a.end()
    rows = captured(exporter)
    assert {s.name for s in rows} == {"A", "HTTP", "B"}
    expected = {s.context.span_id: s for s in witness.get_finished_spans()}
    for s in rows:
        assert s.parent == expected[s.context.span_id].parent
        assert s.start_time == expected[s.context.span_id].start_time
        assert s.end_time == expected[s.context.span_id].end_time
    assert not router().ancestry.nodes
    assert router().default.active == 0


@pytest.mark.parametrize("count", [3, 3000])
def test_five_hour_agent_exports_incrementally(telemetry, count):
    provider, exporter = telemetry
    now = 1_000_000_000
    root = start(provider, "agent", ai=True, start_time=now)
    for index in range(count):
        now = 1_000_000_000 + (index + 1) * (5 * 3600 * 10**9 // count)
        child = start(provider, "model", root, ai=True, start_time=now)
        child.end(end_time=now + 1)
        if index % 100 == 0:
            assert len(captured(exporter)) == index + 1
        assert len(router().ancestry.nodes) == 1
        assert router().ancestry.pending_bytes == 0
    root.end(end_time=now + 2)
    assert len(captured(exporter)) == count + 1
    assert not router().ancestry.nodes


def test_unselected_subtree_discards_after_descendants_finish(telemetry):
    provider, exporter = telemetry
    a = start(provider, "root")
    b = start(provider, "middle", a)
    c = start(provider, "child", b)
    a.end()
    b.end()
    assert len(router().ancestry.pending) == 2
    c.end()
    assert captured(exporter) == ()
    assert router().default.active == 0
    assert router().ancestry.pending_bytes == 0


@pytest.mark.parametrize(
    "limit, exported", [("count", ["late", "middle"]), ("bytes", ["late"])]
)
def test_overflow_drops_oldest_pending_ancestors(
    telemetry, monkeypatch, limit, exported
):
    provider, exporter = telemetry
    monkeypatch.setattr(ancestry, "MAX_PENDING_SPANS", 1 if limit == "count" else 1024)
    monkeypatch.setattr(
        ancestry, "MAX_PENDING_BYTES", 1 if limit == "bytes" else 16 * 1024 * 1024
    )
    a = start(provider, "root")
    b = start(provider, "middle", a)
    c = start(provider, "late", b)
    b.add_event("payload", {"text": "x" * 1024})
    a.end()
    b.end()
    assert router().ancestry.pending_bytes <= ancestry.MAX_PENDING_BYTES
    assert len(router().ancestry.pending) <= ancestry.MAX_PENDING_SPANS
    c.set_attribute("gen_ai.operation.name", "chat")
    c.end()
    # Oldest first: the dropped ancestors never export, even once "late" qualifies.
    rows = captured(exporter)
    assert sorted(s.name for s in rows) == exported
    assert next(s for s in rows if s.name == "late").parent.span_id == (
        b.get_span_context().span_id
    )
    assert not router().ancestry.nodes


@pytest.mark.parametrize("shutdown", [False, True])
def test_lifecycle_never_exports_unresolved_ancestors(telemetry, shutdown):
    provider, exporter = telemetry
    # InMemorySpanExporter clears on shutdown, so observe submitted batches too.
    batches = []
    export = exporter.export
    exporter.export = lambda spans: (batches.extend(spans), export(spans))[1]
    parent = start(provider, "parent")
    child = start(provider, "child", parent)
    parent.end()
    rt = router()
    if shutdown:
        ct.shutdown()
        assert not rt.ancestry.nodes
    else:
        ct.flush()
        assert len(rt.ancestry.pending) == 1
    assert child.is_recording()
    assert batches == []
    child.end()
    if not shutdown:
        ct.flush()
    assert batches == []


def test_real_count_limit(telemetry):
    provider, exporter = telemetry
    children = []
    for _ in range(1025):
        parent = start(provider, "parent")
        children.append(start(provider, "child", parent))
        parent.end()
    assert len(router().ancestry.pending) == 1024
    for child in children:
        child.end()
    assert captured(exporter) == ()
    assert not router().ancestry.nodes
    assert router().default.active == 0


@pytest.mark.parametrize("timeout", [0, 30000])
def test_flush_holds_pending_until_a_descendant_qualifies(telemetry, timeout):
    provider, exporter = telemetry
    parent = start(provider, "parent")
    child = start(provider, "child", parent)
    parent.end()
    router().force_flush(timeout)
    assert len(router().ancestry.pending) == 1
    assert captured(exporter) == ()
    child.set_attribute("gen_ai.operation.name", "chat")
    child.end()
    rows = captured(exporter)
    assert [s.name for s in rows] == ["parent", "child"]
    assert rows[1].parent.span_id == rows[0].context.span_id
    assert not router().ancestry.nodes


def test_pending_ancestor_survives_five_hour_descendant(telemetry):
    provider, exporter = telemetry
    parent = start(provider, "HTTP", start_time=1_000_000_000)
    child = start(provider, "late", parent, start_time=2_000_000_000)
    parent.end(end_time=3_000_000_000)
    assert router().ancestry.pending
    child.set_attribute("gen_ai.operation.name", "chat")
    child.end(end_time=5 * 3600 * 10**9)
    rows = captured(exporter)
    assert [s.name for s in rows] == ["HTTP", "late"]
    assert rows[1].parent.span_id == rows[0].context.span_id
    assert not router().ancestry.nodes
