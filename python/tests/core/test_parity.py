import asyncio
import json
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct

V = json.loads((Path(__file__).parents[3] / "spec/parity-vectors.json").read_text())


def test_fields_and_aliases(telemetry):
    _, exporter = telemetry

    @ct.span(type="llm", **V["llm"])
    def model():
        ct.update_span(
            input="chosen",
            output="explicit",
            expected_output="gold",
            retrieval_context=["doc"],
        )
        ct.update_trace(
            thread=V["thread"], test_case_id=V["test_case_id"], metadata={"trace": True}
        )
        ct.update_llm_span(output_token_count=5)
        return "automatic"

    assert model() == "automatic"
    ct.flush()
    row = exporter.get_finished_spans()[0].attributes
    assert row["confident.span.type"] == "llm"
    assert json.loads(row["confident.span.output"]) == "explicit"
    assert row["gen_ai.usage.input_tokens"] == 0
    assert row["gen_ai.usage.output_tokens"] == 5
    assert row["confident.llm.cost_per_input_token"] == 0
    assert (
        row["confident.trace.thread.id"]
        == row["confident.trace.thread_id"]
        == "chat-42"
    )
    assert row["confident.trace.thread.tags"] == ("conversation",)
    assert json.loads(row["confident.trace.thread.metadata"]) == {"topic": "support"}
    assert json.loads(row["confident.trace.metadata"]) == {"trace": True}
    assert row["confident.trace.test_case_id"] == "case-42"
    with pytest.warns(DeprecationWarning):
        with ct.span(kind="tool"):
            pass
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError):
        ct.span(type="agent", kind="tool")
    for values in (
        {"input_token_count": -1},
        {"output_token_count": 1.5},
        {"cost_per_input_token": float("nan")},
        {"cost_per_output_token": True},
    ):
        with pytest.raises(ValueError):
            ct.update_llm_span(**values)
    with pytest.raises(ValueError):
        ct.update_trace(thread_id="one", thread={"id": "two"})


@pytest.mark.asyncio
async def test_turn_and_scopes(telemetry):
    _, exporter = telemetry
    with ct.span("outer") as outer:
        async with ct.turn(
            thread=V["thread"], previous=outer.get_span_context()
        ) as turn:
            assert turn.get_span_context().trace_id != outer.get_span_context().trace_id
            await asyncio.sleep(0)
            with ct.span(type="tool"):
                pass
    ct.flush()
    rows = exporter.get_finished_spans()
    assert rows[1].links[0].context == outer.get_span_context()
    assert rows[0].attributes["gen_ai.conversation.id"] == "chat-42"
    async with ct.suppress_tracing():
        async with ct.turn(thread_id="hidden"):
            with ct.span("hidden"):
                pass
    ct.flush()
    assert len(exporter.get_finished_spans()) == 3


@pytest.mark.asyncio
async def test_concurrent_project_routing_and_early_export():
    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    fallback = InMemorySpanExporter()
    destinations = {}

    def factory(key):
        destinations[key] = InMemorySpanExporter()
        return destinations[key]

    ct.init(
        tracer_provider=provider,
        exporter=fallback,
        project_exporter_factory=factory,
        instrumentations=(),
    )
    try:

        async def request(key):
            async with ct.project(api_key=key):
                async with ct.span("request-" + key):
                    await asyncio.sleep(0)
                    # An undecorated provider call: routing is captured by the processor.
                    with provider.get_tracer("provider").start_as_current_span(
                        "model-" + key
                    ):
                        pass
                    ct.flush()
                    assert [s.name for s in destinations[key].get_finished_spans()] == [
                        "model-" + key
                    ]
                    with pytest.raises(RuntimeError):
                        with ct.project(api_key="other"):
                            pass

        await asyncio.gather(request("a"), request("b"))
        ct.flush()
        assert not fallback.get_finished_spans()
        for key, output in destinations.items():
            assert {s.name for s in output.get_finished_spans()} == {
                "request-" + key,
                "model-" + key,
            }
            assert all(
                "api_key" not in str(s.attributes) for s in output.get_finished_spans()
            )
        # A span can end after its project scope exits.
        with ct.project(api_key="a"):
            late = provider.get_tracer("provider").start_span("late")
        late.end()
        ct.flush()
        assert destinations["a"].get_finished_spans()[-1].name == "late"
    finally:
        ct.shutdown()
        provider.shutdown()


@pytest.mark.asyncio
async def test_suppression_isolated_and_external_exporter_owned(telemetry):
    provider, exporter = telemetry
    witness = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(witness))

    async def request(hidden):
        if hidden:
            async with ct.suppress_tracing():
                await asyncio.sleep(0)
                with provider.get_tracer("native").start_as_current_span("hidden"):
                    pass
                with ct.suppress_tracing():
                    with ct.span("also-hidden"):
                        pass
        else:
            await asyncio.sleep(0)
            with ct.span("visible"):
                pass

    await asyncio.gather(request(True), request(False))
    ct.flush()
    assert [s.name for s in exporter.get_finished_spans()] == ["visible"]
    assert {s.name for s in witness.get_finished_spans()} == {"hidden", "visible"}
    with pytest.raises(ValueError):
        with ct.suppress_tracing():
            raise ValueError("application error")
    with ct.span("restored"):
        pass
    ct.flush()
    assert exporter.get_finished_spans()[-1].name == "restored"


def test_custom_exporter_requires_factory_and_updates_need_active_span(telemetry):
    _, exporter = telemetry
    with pytest.raises(RuntimeError):
        with ct.project(api_key="tenant"):
            pass
    ct.update_trace(thread=V["thread"])
    ct.update_llm_span(model="none")
    ct.flush()
    assert not exporter.get_finished_spans()


def test_generator_parent_and_project_captured_at_call():
    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    target = InMemorySpanExporter()
    ct.init(
        tracer_provider=provider,
        exporter=exporter,
        project_exporter_factory=lambda _: target,
        instrumentations=(),
    )

    @ct.span(type="tool")
    def generate():
        yield 1

    try:
        with ct.project(api_key="tenant"):
            stream = generate()
        assert list(stream) == [1]
        ct.flush()
        assert len(target.get_finished_spans()) == 1
        assert not exporter.get_finished_spans()
    finally:
        ct.shutdown()


def test_thread_metadata_obeys_content_policy():
    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=provider,
        exporter=exporter,
        capture_content=False,
        instrumentations=(),
    )
    try:
        with ct.span():
            ct.update_trace(thread=V["thread"])
        ct.flush()
        attrs = exporter.get_finished_spans()[0].attributes
        assert "confident.trace.thread.metadata" not in attrs
        assert attrs["confident.trace.thread.id"] == "chat-42"
    finally:
        ct.shutdown()


def test_real_openai_client_scopes_without_application_decorator():
    import httpx
    from openai import OpenAI

    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    fallback, target = InMemorySpanExporter(), InMemorySpanExporter()
    rt = ct.init(
        tracer_provider=provider,
        exporter=fallback,
        project_exporter_factory=lambda _: target,
        instrumentations=("openai",),
    )
    client = OpenAI(
        api_key="fake",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "id": "fake",
                        "model": "gpt-test",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "hello"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                    },
                )
            )
        ),
    )

    def answer():
        return client.chat.completions.create(
            model="gpt-test", messages=[{"role": "user", "content": "hi"}]
        )

    try:
        with ct.suppress_tracing():
            assert ct.init() is rt
            answer()
        with ct.project(api_key="tenant"):
            answer()
        ct.flush()
        assert not fallback.get_finished_spans()
        assert len(target.get_finished_spans()) == 1
        assert (
            target.get_finished_spans()[0].attributes["gen_ai.usage.input_tokens"] == 2
        )
    finally:
        client.close()
        ct.shutdown()


def test_route_cache_protects_active_spans_and_reopens_old_context():
    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    fallback = InMemorySpanExporter()
    targets = []

    def factory(key):
        value = InMemorySpanExporter()
        targets.append((key, value))
        return value

    rt = ct.init(
        tracer_provider=provider,
        exporter=fallback,
        project_exporter_factory=factory,
        instrumentations=(),
    )

    @ct.span
    def delayed():
        yield "ok"

    try:
        with ct.project(api_key="old"):
            generator = delayed()
        with ct.project(api_key="active"):
            active = provider.get_tracer("native").start_span("active")
        for i in range(70):
            with ct.project(api_key=str(i)):
                with ct.span("request"):
                    pass
        assert len(rt.processor.delegate.routes) <= 65
        assert "active" in rt.processor.delegate.routes
        assert list(generator) == ["ok"]
        active.end()
        ct.flush()
        assert not fallback.get_finished_spans()
        assert sum(len(e.get_finished_spans()) for k, e in targets if k == "old") == 1
    finally:
        ct.shutdown()


def test_failed_route_does_not_fall_back_or_expose_secret():
    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    fallback = InMemorySpanExporter()

    def fail(key):
        raise ValueError(key)

    ct.init(
        tracer_provider=provider,
        exporter=fallback,
        project_exporter_factory=fail,
        instrumentations=(),
    )
    try:
        with pytest.raises(RuntimeError) as error:
            with ct.project(api_key="secret-credential"):
                pytest.fail("must not execute with wrong routing")
        assert "secret-credential" not in str(error.value)
        assert not fallback.get_finished_spans()
    finally:
        ct.shutdown()


def test_project_auth_overrides_default_header_and_disabled_is_noop(monkeypatch):
    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    rt = ct.init(
        tracer_provider=provider,
        endpoint="http://127.0.0.1:1/v1/traces",
        api_key="default",
        headers={"X-Confident-Api-Key": "header-default"},
        instrumentations=(),
    )
    try:
        with ct.project(api_key="default"):
            exporter = rt.processor.delegate.routes["default"].processor.span_exporter
            assert exporter._headers["x-confident-api-key"] == "default"
        monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
        with ct.project(api_key="disabled"):
            assert "disabled" not in rt.processor.delegate.routes
    finally:
        ct.shutdown()


def test_delayed_generator_respects_suppression_at_consumption(telemetry):
    _, exporter = telemetry

    @ct.span
    def generate():
        with ct.span("inner"):
            yield 1

    stream = generate()
    with ct.suppress_tracing():
        assert list(stream) == [1]
    ct.flush()
    assert not exporter.get_finished_spans()


def test_unified_updates_preserve_general_fields_and_warn_once(
    telemetry, monkeypatch, caplog
):
    from confident_trace._core import spans

    monkeypatch.setattr(spans, "_warned_llm_targets", set())
    _, exporter = telemetry
    with ct.span(type="tool"):
        ct.update_span(output="kept", model="private-model", input_token_count=7)
        ct.update_llm_span(output_token_count=2)
    with ct.span(type="llm"):
        ct.update_span(output="answer", model="model", input_token_count=0)
        ct.update_llm_span(output_token_count=2)
    with (
        telemetry[0]
        .get_tracer("native")
        .start_as_current_span("native", attributes={"gen_ai.operation.name": "chat"})
    ):
        ct.update_span(model="native-model")
    ct.flush()
    tool, llm, native = exporter.get_finished_spans()
    assert json.loads(tool.attributes["confident.span.output"]) == "kept"
    assert "gen_ai.request.model" not in tool.attributes
    assert "gen_ai.usage.output_tokens" not in tool.attributes
    assert llm.attributes["gen_ai.usage.input_tokens"] == 0
    assert llm.attributes["gen_ai.usage.output_tokens"] == 2
    assert native.attributes["gen_ai.request.model"] == "native-model"
    warnings = [r.message for r in caplog.records if "skipped LLM fields" in r.message]
    assert len(warnings) == 1
    assert "private-model" not in warnings[0]


@pytest.mark.parametrize("category", ["agent", "llm", "retriever", "tool", "custom"])
def test_unified_updates_on_every_category(category, telemetry, monkeypatch, caplog):
    from confident_trace._core import spans

    monkeypatch.setattr(spans, "_warned_llm_targets", set())
    _, exporter = telemetry
    with ct.span(type=category):
        ct.update_span(
            input="question",
            output="answer",
            metadata={"source": "test"},
            context=["reference"],
            retrieval_context=["document"],
            expected_output="answer",
            tools_called=[{"name": "lookup"}],
            expected_tools=[{"name": "lookup"}],
            model="test",
            input_token_count=0,
        )
        ct.update_span(output_token_count=1)
    ct.flush()
    attrs = exporter.get_finished_spans()[0].attributes
    for key, value in {
        "input": "question",
        "output": "answer",
        "metadata": {"source": "test"},
        "context": ["reference"],
        "retrieval_context": ["document"],
        "expected_output": "answer",
        "tools_called": [{"name": "lookup"}],
        "expected_tools": [{"name": "lookup"}],
    }.items():
        assert json.loads(attrs[f"confident.span.{key}"]) == value
    assert ("gen_ai.request.model" in attrs) == (category == "llm")
    assert len([r for r in caplog.records if "skipped LLM fields" in r.message]) == (
        0 if category == "llm" else 1
    )
