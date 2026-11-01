"""One process per scenario: the global tracer provider never escapes it."""

import gzip
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
    # A Pipecat span on another provider does not stand our provider span down.
    assert by_scope(captured, "pipecat", "pipecat.turn") == []
    assert len(by_scope(captured, "confident_trace")) == 1
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


CONTENT = (
    "input",
    "output",
    "transcript",
    "text",
    "instructions",
    "session.instructions",
)


def speech_spans():
    # stt and tts spans need audio services; write them the way Pipecat does.
    tracer = trace.get_tracer("pipecat")
    with tracer.start_as_current_span("stt") as span:
        span.set_attribute("transcript", "Weather in Paris?")
        span.set_attribute("language", "en")
    with tracer.start_as_current_span("tts") as span:
        span.set_attribute("text", "It is sunny in Paris.")
        span.set_attribute("metrics.character_count", 21)
    with tracer.start_as_current_span("llm_setup") as span:
        span.set_attribute("instructions", "You are a Paris weather bot.")
        span.set_attribute("session.instructions", "You are a Paris weather bot.")
        span.set_attribute("session.voice", "alloy")


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
    assert named["llm_setup"]["session.voice"] == "alloy"
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
    from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

    init, cleanup = PipelineWorker.__init__, PipelineWorker._cleanup
    setup, start = AudioBufferProcessor.setup, AudioBufferProcessor.start_recording
    ct.init(exporter=InMemorySpanExporter(), instrumentations=("pipecat",))
    assert PipelineWorker.__init__ is not init
    assert AudioBufferProcessor.setup is not setup
    ct.shutdown()
    assert PipelineWorker.__init__ is init
    assert PipelineWorker._cleanup is cleanup
    assert AudioBufferProcessor.setup is setup
    assert AudioBufferProcessor.start_recording is start


class Receiver:
    """A local Confident edge that records span batches and call-recording uploads."""

    def __init__(self, delay=0.0):
        import http.server
        import threading

        received = self.received = []
        paths = self.paths = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["content-length"]))
                if self.path.startswith("/v1/call-recordings"):
                    time.sleep(delay)
                    received.append(
                        (
                            self.path,
                            {k.lower(): v for k, v in self.headers.items()},
                            body,
                        )
                    )
                    paths.append("recording")
                else:
                    if self.headers.get("content-encoding") == "gzip":
                        body = gzip.decompress(body)
                    paths.append(span_names(body))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/v1/traces"


def span_names(body):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    request = ExportTraceServiceRequest()
    request.ParseFromString(body)
    return {
        span.name: span.trace_id.hex()
        for resource in request.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    }


def tone(seconds=1, sample_rate=24000):
    import numpy

    samples = numpy.arange(seconds * sample_rate) / sample_rate
    return (
        (numpy.sin(samples * 2 * numpy.pi * 440) * 8000).astype(numpy.int16).tobytes()
    )


async def record_call(
    *,
    auto_start_recording=True,
    with_recorder=True,
    sample_rate=24000,
    on_audio_data=None,
    buffer_size=0,
    **worker_kwargs,
):
    from pipecat.frames.frames import EndFrame, InputAudioRawFrame, OutputAudioRawFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.workers.runner import WorkerRunner

    audio = AudioBufferProcessor(
        num_channels=2,
        auto_start_recording=auto_start_recording,
        buffer_size=buffer_size,
    )
    if on_audio_data is not None:
        audio.add_event_handler("on_audio_data", on_audio_data)
    processor = audio if with_recorder else FrameProcessor()
    worker = PipelineWorker(
        Pipeline([processor]),
        params=PipelineParams(audio_out_sample_rate=sample_rate),
        conversation_id="call-1",
        **worker_kwargs,
    )
    chunk = tone(sample_rate=sample_rate)
    frames = []
    for _ in range(3):
        frames.append(
            InputAudioRawFrame(audio=chunk, sample_rate=sample_rate, num_channels=1)
        )
        frames.append(
            OutputAudioRawFrame(audio=chunk, sample_rate=sample_rate, num_channels=1)
        )
    await worker.queue_frames([*frames, EndFrame()])
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


@pytest.mark.asyncio
async def test_call_recording_is_uploaded_to_the_conversation_trace(tmp_path):
    import soundfile

    receiver = Receiver()
    ct.init(endpoint=receiver.endpoint, api_key="key", instrumentations=("pipecat",))
    before = time.time()
    await record_call()
    after = time.time()
    [(path, headers, body)] = receiver.received
    batches = [batch for batch in receiver.paths if batch != "recording"]
    (trace_uuid,) = {b["conversation"] for b in batches if "conversation" in b}
    query = path.split("?", 1)[1]
    assert path.startswith("/v1/call-recordings?")
    assert f"traceUuid={trace_uuid}" in query
    started_at = int(query.split("startedAt=", 1)[1]) / 1000
    assert before <= started_at <= after
    assert headers["x-confident-api-key"] == "key"
    assert headers["content-type"] == "audio/ogg"
    recording = tmp_path / "call.ogg"
    recording.write_bytes(body)
    info = soundfile.info(recording)
    assert (info.format, info.subtype, info.channels) == ("OGG", "OPUS", 2)
    assert info.duration > 2.5
    ct.shutdown()


@pytest.mark.asyncio
async def test_call_recording_is_uploaded_before_the_final_flush():
    receiver = Receiver()
    ct.init(endpoint=receiver.endpoint, api_key="key", instrumentations=("pipecat",))
    await record_call()
    order = [
        "recording" if batch == "recording" else "conversation" in batch
        for batch in receiver.paths
    ]
    assert order.index("recording") < order.index(True)
    ct.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "not_started",
        "no_recorder",
        "content_off",
        "tracing_off",
        "pipecat_unselected",
        "unsupported_sample_rate",
    ],
)
async def test_call_recording_is_skipped(case):
    receiver = Receiver()
    ct.init(
        endpoint=receiver.endpoint,
        api_key="key",
        instrumentations=() if case == "pipecat_unselected" else ("pipecat",),
        capture_content=case != "content_off",
    )
    await record_call(
        auto_start_recording=case != "not_started",
        with_recorder=case != "no_recorder",
        sample_rate=44100 if case == "unsupported_sample_rate" else 24000,
        **({"enable_tracing": False} if case == "tracing_off" else {}),
    )
    assert receiver.received == []
    ct.shutdown()


@pytest.mark.asyncio
async def test_user_audio_handlers_keep_receiving_audio():
    receiver = Receiver()
    ct.init(endpoint=receiver.endpoint, api_key="key", instrumentations=("pipecat",))
    delivered = []

    async def on_audio_data(processor, audio, sample_rate, num_channels):
        delivered.append((len(audio), sample_rate, num_channels))

    await record_call(on_audio_data=on_audio_data)
    [(size, sample_rate, num_channels)] = delivered
    assert size >= 3 * 24000 * 2 * 2
    assert (sample_rate, num_channels) == (24000, 2)
    assert len(receiver.received) == 1
    ct.shutdown()


@pytest.mark.asyncio
async def test_call_recording_upload_is_bounded():
    receiver = Receiver(delay=10)
    ct.init(endpoint=receiver.endpoint, api_key="key", instrumentations=("pipecat",))
    start = time.monotonic()
    await record_call()
    assert 2.5 < time.monotonic() - start < 6
    ct.shutdown()


@pytest.mark.asyncio
async def test_buffered_audio_is_uploaded_as_one_recording(tmp_path):
    import soundfile

    receiver = Receiver()
    ct.init(endpoint=receiver.endpoint, api_key="key", instrumentations=("pipecat",))
    delivered = []

    async def on_audio_data(processor, audio, sample_rate, num_channels):
        delivered.append(len(audio))

    await record_call(on_audio_data=on_audio_data, buffer_size=24000 * 2)
    assert len(delivered) > 1
    [(_, _, body)] = receiver.received
    recording = tmp_path / "call.ogg"
    recording.write_bytes(body)
    assert soundfile.info(recording).duration > 2.5
    ct.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body, test_case_id",
    [
        ({"confident_test_case_id": "case-1"}, "case-1"),
        ({"caller": "+15550100"}, None),
        ({}, None),
    ],
)
async def test_start_body_links_the_trace_to_its_test_case(body, test_case_id):
    from pipecat.runner.types import RunnerArguments

    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("pipecat",))
    RunnerArguments(body=body)
    await converse()
    ct.flush()
    (conversation,) = [
        s for s in exporter.get_finished_spans() if s.name == "conversation"
    ]
    assert conversation.attributes.get("confident.trace.test_case_id") == test_case_id
    ct.shutdown()
