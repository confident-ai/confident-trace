"""One process per scenario: LiveKit's module-level tracer never escapes it."""

import json

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct

USAGE = {"prompt_tokens": 120, "completion_tokens": 12, "total_tokens": 132}


def sse(chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def chunk(delta, finish=None):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "gpt-4.1-mini",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def fake_openai(request):
    messages = json.loads(request.content)["messages"]
    usage = {**chunk({}), "choices": [], "usage": USAGE}
    if not any(m.get("role") == "tool" for m in messages):
        call = {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "lookup_weather", "arguments": '{"city": "Paris"}'},
        }
        return sse(
            [
                chunk({"role": "assistant", "tool_calls": [call]}),
                chunk({}, "tool_calls"),
                usage,
            ]
        )
    return sse(
        [
            chunk({"role": "assistant", "content": "It is sunny."}),
            chunk({}, "stop"),
            usage,
        ]
    )


async def converse():
    import openai as openai_sdk
    from livekit.agents import Agent, AgentSession, function_tool
    from livekit.plugins import openai

    class Assistant(Agent):
        def __init__(self):
            super().__init__(instructions="You are a helpful voice assistant.")

        @function_tool
        async def lookup_weather(self, city: str) -> str:
            """Look up the weather for a city."""
            return f"Sunny in {city}"

    client = openai_sdk.AsyncOpenAI(
        api_key="offline",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)),
    )
    llm = openai.LLM(model="gpt-4.1-mini", client=client)
    async with AgentSession(llm=llm) as session:
        await session.start(Assistant())
        await session.run(user_input="What's the weather in Paris?")


def by_scope(captured, name):
    return [s for s in captured if s.instrumentation_scope.name == name]


@pytest.mark.asyncio
async def test_session_spans_are_labelled_and_llm_calls_appear_once():
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("livekit", "openai"))
    await converse()
    ct.flush()
    captured = exporter.get_finished_spans()
    livekit = by_scope(captured, "livekit-agents")
    names = {s.name for s in livekit}
    # Voice-only lifecycle spans carry no GenAI data but are still exported.
    assert {"agent_session", "on_enter", "llm_request_run"} <= names
    assert all(s.attributes["confident.span.integration"] == "LiveKit" for s in livekit)
    assert [s.name for s in livekit].count("llm_request") == 2
    assert by_scope(captured, "confident_trace") == []
    ids = {s.context.span_id for s in captured}
    assert all(s.parent is None or s.parent.span_id in ids for s in captured)


@pytest.mark.asyncio
async def test_unconfigured_livekit_tracer_uses_our_provider():
    from livekit.agents import telemetry

    provider = TracerProvider(shutdown_on_exit=False)
    ct.init(
        tracer_provider=provider,
        exporter=InMemorySpanExporter(),
        instrumentations=("livekit",),
    )
    assert telemetry.tracer._tracer_provider is provider


@pytest.mark.asyncio
async def test_configured_livekit_tracer_is_preserved():
    from livekit.agents import telemetry

    other = TracerProvider(shutdown_on_exit=False)
    elsewhere = InMemorySpanExporter()
    other.add_span_processor(SimpleSpanProcessor(elsewhere))
    telemetry.set_tracer_provider(other)
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("livekit", "openai"))
    assert telemetry.tracer._tracer_provider is other
    await converse()
    ct.flush()
    captured = exporter.get_finished_spans()
    # A LiveKit span on another provider does not stand our provider span down.
    assert by_scope(captured, "livekit-agents") == []
    assert len(by_scope(captured, "confident_trace")) == 2
    assert by_scope(elsewhere.get_finished_spans(), "livekit-agents")


@pytest.mark.asyncio
async def test_unselected_livekit_keeps_provider_spans():
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("openai",))
    await converse()
    ct.flush()
    captured = exporter.get_finished_spans()
    assert len(by_scope(captured, "confident_trace")) == 2
    livekit = by_scope(captured, "livekit-agents")
    assert livekit
    assert all("confident.span.integration" not in s.attributes for s in livekit)


@pytest.mark.parametrize("import_first", [False, True])
def test_privacy_import_order(import_first, monkeypatch):
    monkeypatch.setenv("LIVEKIT_TELEMETRY_ALLOW_PII", "0")
    if import_first:
        from livekit.agents import telemetry
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("livekit",))
    from livekit.agents import telemetry

    with telemetry.tracer.start_as_current_span("agent_session") as span:
        span.set_attribute("lk.pii.user_transcript", "private transcript")
        span.set_attribute("gen_ai.input.messages", "private prompt")
    ct.flush()
    attrs = exporter.get_finished_spans()[0].attributes
    assert "lk.pii.user_transcript" not in attrs
    assert "gen_ai.input.messages" not in attrs


@pytest.mark.asyncio
async def test_cleanup_flush_preserves_exception(monkeypatch):
    from livekit.agents import JobContext, telemetry

    failure = ValueError("cleanup failed")

    async def cleanup(self):
        with telemetry.tracer.start_as_current_span("cleanup"):
            pass
        raise failure

    monkeypatch.setattr(JobContext, "_on_cleanup", cleanup)
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("livekit",))
    with pytest.raises(ValueError) as caught:
        await JobContext._on_cleanup(object())
    assert caught.value is failure
    assert [s.name for s in exporter.get_finished_spans()] == ["cleanup"]
    ct.shutdown()
    assert JobContext._on_cleanup is cleanup


@pytest.mark.asyncio
async def test_cleanup_flush_is_bounded(monkeypatch):
    import time

    from livekit.agents import JobContext

    async def cleanup(self):
        return "finished"

    monkeypatch.setattr(JobContext, "_on_cleanup", cleanup)
    runtime = ct.init(exporter=InMemorySpanExporter(), instrumentations=("livekit",))

    def stuck_flush(timeout):
        assert timeout == 5000
        time.sleep(10)
        return True

    monkeypatch.setattr(runtime.processor, "force_flush", stuck_flush)
    start = time.monotonic()
    assert await JobContext._on_cleanup(object()) == "finished"
    assert 4.5 < time.monotonic() - start < 7
    ct.shutdown()


@pytest.mark.asyncio
async def test_flush_failure_does_not_replace_cleanup_error(monkeypatch):
    from livekit.agents import JobContext

    failure = ValueError("cleanup failed")

    async def cleanup(self):
        raise failure

    monkeypatch.setattr(JobContext, "_on_cleanup", cleanup)
    runtime = ct.init(exporter=InMemorySpanExporter(), instrumentations=("livekit",))

    def broken_flush(timeout):
        raise RuntimeError("export failed")

    monkeypatch.setattr(runtime.processor, "force_flush", broken_flush)
    with pytest.raises(ValueError) as caught:
        await JobContext._on_cleanup(object())
    assert caught.value is failure
    ct.shutdown()


class Receiver:
    """A local Confident edge that records call-recording uploads."""

    def __init__(self, delay=0.0):
        import http.server
        import threading

        received = self.received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["content-length"]))
                if self.path.startswith("/v1/call-recordings"):
                    import time

                    time.sleep(delay)
                    received.append(
                        (
                            self.path,
                            {k.lower(): v for k, v in self.headers.items()},
                            body,
                        )
                    )
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/v1/traces"


def fake_job_context(monkeypatch, recording_path, started_at=1788652800.5):
    from types import SimpleNamespace

    import livekit.agents

    callbacks = []
    report = SimpleNamespace(
        audio_recording_path=recording_path,
        audio_recording_started_at=started_at if recording_path else None,
    )

    class FakeJobContext:
        job = SimpleNamespace(room=SimpleNamespace(sid="RM_room"))
        add_shutdown_callback = staticmethod(callbacks.append)

        def make_session_report(self):
            return report

    ctx = FakeJobContext()
    monkeypatch.setattr(livekit.agents, "get_job_context", lambda required=True: ctx)
    return callbacks


def recording(tmp_path):
    path = tmp_path / "audio.ogg"
    path.write_bytes(b"OggS-call-audio")
    return path


@pytest.mark.asyncio
async def test_call_recording_is_uploaded(monkeypatch, tmp_path):
    from confident_trace.integrations.livekit.recording import (
        register_recording_upload,
    )

    receiver = Receiver()
    runtime = ct.init(
        endpoint=receiver.endpoint, api_key="key", instrumentations=("livekit",)
    )
    callbacks = fake_job_context(monkeypatch, recording(tmp_path))
    register_recording_upload(runtime)
    register_recording_upload(runtime)
    assert len(callbacks) == 1
    await callbacks[0]()
    [(path, headers, body)] = receiver.received
    assert path == "/v1/call-recordings?threadId=RM_room&startedAt=1788652800500"
    assert headers["x-confident-api-key"] == "key"
    assert headers["content-type"] == "audio/ogg"
    assert body == b"OggS-call-audio"
    ct.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["not_recorded", "content_off", "pii_off"])
async def test_call_recording_is_skipped(case, monkeypatch, tmp_path):
    from confident_trace.integrations.livekit.recording import (
        register_recording_upload,
    )

    if case == "pii_off":
        monkeypatch.setenv("LIVEKIT_TELEMETRY_ALLOW_PII", "0")
    receiver = Receiver()
    runtime = ct.init(
        endpoint=receiver.endpoint,
        api_key="key",
        instrumentations=("livekit",),
        capture_content=case != "content_off",
    )
    path = None if case == "not_recorded" else recording(tmp_path)
    callbacks = fake_job_context(monkeypatch, path)
    register_recording_upload(runtime)
    await callbacks[0]()
    assert receiver.received == []
    ct.shutdown()


@pytest.mark.asyncio
async def test_call_recording_upload_is_bounded(monkeypatch, tmp_path):
    import time

    from confident_trace.integrations.livekit.recording import (
        register_recording_upload,
    )

    receiver = Receiver(delay=10)
    runtime = ct.init(
        endpoint=receiver.endpoint, api_key="key", instrumentations=("livekit",)
    )
    callbacks = fake_job_context(monkeypatch, recording(tmp_path))
    register_recording_upload(runtime)
    start = time.monotonic()
    await callbacks[0]()
    assert 2.5 < time.monotonic() - start < 5
    ct.shutdown()


@pytest.mark.asyncio
async def test_session_start_outside_a_job_registers_nothing():
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("livekit", "openai"))
    await converse()
    ct.flush()
    assert by_scope(exporter.get_finished_spans(), "livekit-agents")
    ct.shutdown()
