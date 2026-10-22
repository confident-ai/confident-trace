"""One process per scenario: the global tracer provider never escapes it."""

import json
import time

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct

USAGE = {"prompt_tokens": 120, "completion_tokens": 12, "total_tokens": 132}


def chunk(delta, finish=None):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-4.1-mini",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def fake_openai(request):
    chunks = [
        chunk({"role": "assistant", "content": "It is sunny."}),
        chunk({}, "stop"),
        {**chunk({}), "choices": [], "usage": USAGE},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


async def converse(**worker_kwargs):
    import openai as openai_sdk
    from pipecat.frames.frames import (
        EndFrame,
        LLMContextFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.workers.runner import WorkerRunner

    llm = OpenAILLMService(
        api_key="offline", settings=OpenAILLMService.Settings(model="gpt-4.1-mini")
    )
    llm._client = openai_sdk.AsyncOpenAI(
        api_key="offline",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)),
    )
    worker = PipelineWorker(Pipeline([llm]), conversation_id="call-1", **worker_kwargs)
    context = LLMContext(messages=[{"role": "user", "content": "Weather in Paris?"}])
    await worker.queue_frames(
        [
            UserStartedSpeakingFrame(),
            UserStoppedSpeakingFrame(),
            LLMContextFrame(context),
            EndFrame(),
        ]
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


def by_scope(captured, *names):
    return [s for s in captured if s.instrumentation_scope.name in names]


@pytest.mark.asyncio
async def test_conversation_spans_are_labelled_and_llm_calls_appear_once():
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("pipecat", "openai"))
    # Tracing is on without enable_tracing=True.
    await converse()
    ct.flush()
    captured = exporter.get_finished_spans()
    pipecat = by_scope(captured, "pipecat", "pipecat.turn")
    assert {"conversation", "turn", "llm"} <= {s.name for s in pipecat}
    assert all(s.attributes["confident.span.integration"] == "Pipecat" for s in pipecat)
    (llm,) = [s for s in pipecat if s.name == "llm"]
    assert llm.attributes["gen_ai.usage.input_tokens"] == 120
    (conversation,) = [s for s in pipecat if s.name == "conversation"]
    assert conversation.attributes["conversation.id"] == "call-1"
    assert by_scope(captured, "confident_trace") == []
    assert {s.context.trace_id for s in captured} == {conversation.context.trace_id}
    ids = {s.context.span_id for s in captured}
    assert all(s.parent is None or s.parent.span_id in ids for s in captured)


@pytest.mark.asyncio
async def test_explicit_enable_tracing_false_is_respected():
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("pipecat", "openai"))
    await converse(enable_tracing=False)
    ct.flush()
    captured = exporter.get_finished_spans()
    assert by_scope(captured, "pipecat", "pipecat.turn") == []
    assert len(by_scope(captured, "confident_trace")) == 1


@pytest.mark.asyncio
async def test_unselected_pipecat_leaves_tracing_off():
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("openai",))
    await converse()
    ct.flush()
    captured = exporter.get_finished_spans()
    assert by_scope(captured, "pipecat", "pipecat.turn") == []
    assert len(by_scope(captured, "confident_trace")) == 1


@pytest.mark.asyncio
async def test_unselected_pipecat_with_tracing_keeps_provider_spans():
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("openai",))
    await converse(enable_tracing=True)
    ct.flush()
    captured = exporter.get_finished_spans()
    assert len(by_scope(captured, "confident_trace")) == 1
    pipecat = by_scope(captured, "pipecat", "pipecat.turn")
    assert pipecat
    assert all("confident.span.integration" not in s.attributes for s in pipecat)


def test_passed_provider_becomes_global_when_unset():
    provider = TracerProvider(shutdown_on_exit=False)
    ct.init(
        tracer_provider=provider,
        exporter=InMemorySpanExporter(),
        instrumentations=("pipecat",),
    )
    assert trace.get_tracer_provider() is provider


@pytest.mark.asyncio
async def test_configured_global_provider_is_preserved():
    other = TracerProvider(shutdown_on_exit=False)
    elsewhere = InMemorySpanExporter()
    other.add_span_processor(SimpleSpanProcessor(elsewhere))
    trace.set_tracer_provider(other)
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=TracerProvider(shutdown_on_exit=False),
        exporter=exporter,
        instrumentations=("pipecat", "openai"),
    )
    assert trace.get_tracer_provider() is other
    await converse()
    ct.flush()
    captured = exporter.get_finished_spans()
    pipecat = by_scope(captured, "pipecat", "pipecat.turn")
    assert {"conversation", "turn", "llm"} <= {s.name for s in pipecat}
    assert by_scope(captured, "confident_trace") == []
    assert by_scope(elsewhere.get_finished_spans(), "pipecat.turn")


@pytest.mark.asyncio
async def test_cleanup_flushes_after_the_conversation_ends(monkeypatch):
    exporter = InMemorySpanExporter()
    runtime = ct.init(exporter=exporter, instrumentations=("pipecat",))
    flush = runtime.processor.force_flush
    seen = []

    def recording_flush(timeout):
        assert timeout == 5000
        result = flush(timeout)
        seen.append([s.name for s in exporter.get_finished_spans()])
        return result

    monkeypatch.setattr(runtime.processor, "force_flush", recording_flush)
    await converse()
    assert seen and "conversation" in seen[-1]


@pytest.mark.asyncio
async def test_cleanup_flush_is_bounded(monkeypatch):
    runtime = ct.init(exporter=InMemorySpanExporter(), instrumentations=("pipecat",))

    def stuck_flush(timeout):
        time.sleep(10)
        return True

    monkeypatch.setattr(runtime.processor, "force_flush", stuck_flush)
    start = time.monotonic()
    await converse()
    assert 4.5 < time.monotonic() - start < 7
    ct.shutdown()


@pytest.mark.asyncio
async def test_flush_failure_does_not_break_cleanup(monkeypatch):
    runtime = ct.init(exporter=InMemorySpanExporter(), instrumentations=("pipecat",))

    def broken_flush(timeout):
        raise RuntimeError("export failed")

    monkeypatch.setattr(runtime.processor, "force_flush", broken_flush)
    await converse()
    ct.shutdown()


CONTENT = ("input", "output", "transcript", "text")


def speech_spans():
    # stt and tts spans need audio services; write them the way Pipecat does.
    tracer = trace.get_tracer("pipecat")
    with tracer.start_as_current_span("stt") as span:
        span.set_attribute("transcript", "Weather in Paris?")
        span.set_attribute("language", "en")
    with tracer.start_as_current_span("tts") as span:
        span.set_attribute("text", "It is sunny in Paris.")
        span.set_attribute("metrics.character_count", 21)


@pytest.mark.asyncio
async def test_capture_content_off_drops_pipecat_content():
    exporter = InMemorySpanExporter()
    ct.init(
        exporter=exporter,
        instrumentations=("pipecat", "openai"),
        capture_content=False,
    )
    await converse()
    speech_spans()
    ct.flush()
    pipecat = by_scope(exporter.get_finished_spans(), "pipecat", "pipecat.turn")
    assert {"conversation", "turn", "llm", "stt", "tts"} <= {s.name for s in pipecat}
    for span in pipecat:
        assert not set(CONTENT) & set(span.attributes), span.name
        assert span.attributes["confident.span.integration"] == "Pipecat"
    named = {s.name: s.attributes for s in pipecat}
    assert named["llm"]["gen_ai.usage.input_tokens"] == 120
    assert named["llm"]["gen_ai.request.model"] == "gpt-4.1-mini"
    assert named["stt"]["language"] == "en"
    assert named["tts"]["metrics.character_count"] == 21
    assert named["conversation"]["conversation.id"] == "call-1"


def scrub(value):
    if isinstance(value, str):
        return value.replace("Paris", "[city]")
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    return value


@pytest.mark.asyncio
async def test_redactor_rewrites_pipecat_content():
    exporter = InMemorySpanExporter()
    seen = []

    def redact(value):
        seen.append(type(value))
        return scrub(value)

    ct.init(exporter=exporter, instrumentations=("pipecat", "openai"), redact=redact)
    await converse()
    speech_spans()
    ct.flush()
    named = {
        s.name: s.attributes
        for s in by_scope(exporter.get_finished_spans(), "pipecat", "pipecat.turn")
    }
    # JSON text reaches the redactor as structured messages, and stays JSON.
    assert list in seen
    messages = json.loads(named["llm"]["input"])
    assert messages[-1]["content"] == "Weather in [city]?"
    assert named["llm"]["output"] == "It is sunny."
    assert named["stt"]["transcript"] == "Weather in [city]?"
    assert named["tts"]["text"] == "It is sunny in [city]."
    assert "Paris" not in json.dumps({k: dict(v) for k, v in named.items()})


@pytest.mark.asyncio
async def test_failing_redactor_omits_pipecat_content():
    exporter = InMemorySpanExporter()

    def redact(value):
        raise ValueError("redactor failed")

    ct.init(exporter=exporter, instrumentations=("pipecat", "openai"), redact=redact)
    await converse()
    speech_spans()
    ct.flush()
    pipecat = by_scope(exporter.get_finished_spans(), "pipecat", "pipecat.turn")
    assert {"llm", "stt", "tts"} <= {s.name for s in pipecat}
    for span in pipecat:
        assert not set(CONTENT) & set(span.attributes), span.name


@pytest.mark.asyncio
async def test_content_is_kept_by_default():
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("pipecat", "openai"))
    await converse()
    speech_spans()
    ct.flush()
    named = {
        s.name: s.attributes
        for s in by_scope(exporter.get_finished_spans(), "pipecat", "pipecat.turn")
    }
    assert "Paris" in named["llm"]["input"]
    assert named["stt"]["transcript"] == "Weather in Paris?"


def test_shutdown_restores_pipecat():
    from pipecat.pipeline.worker import PipelineWorker

    init, cleanup = PipelineWorker.__init__, PipelineWorker._cleanup
    ct.init(exporter=InMemorySpanExporter(), instrumentations=("pipecat",))
    assert PipelineWorker.__init__ is not init
    ct.shutdown()
    assert PipelineWorker.__init__ is init
    assert PipelineWorker._cleanup is cleanup
