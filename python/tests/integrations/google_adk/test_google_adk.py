import asyncio
import json
from contextlib import aclosing
from functools import cached_property

import httpx
import pytest
from conftest import spans

pytest.importorskip("google.adk")
from google import genai
from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.models.google_llm import Gemini
from google.adk.runners import InMemoryRunner
from google.genai import types
from opentelemetry import trace

import confident_trace as ct


def response(parts):
    return {
        "candidates": [
            {"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
    }


def make_runner(handler, tools=()):
    client = genai.Client(
        api_key="offline",
        http_options=types.HttpOptions(
            async_client_args={"transport": httpx.MockTransport(handler)}
        ),
    )

    class LocalGemini(Gemini):
        @cached_property
        def api_client(self):
            return client

    runner = InMemoryRunner(
        app_name="native-test",
        agent=LlmAgent(
            name="assistant",
            model=LocalGemini(model="gemini-2.5-flash"),
            tools=list(tools),
        ),
    )
    return runner, client


async def run(runner, session="session", stream=False):
    await runner.session_service.create_session(
        app_name="native-test", user_id="user", session_id=session
    )
    async with aclosing(
        runner.run_async(
            user_id="user",
            session_id=session,
            new_message=types.Content(role="user", parts=[types.Part(text=session)]),
            run_config=RunConfig(
                streaming_mode=StreamingMode.SSE if stream else StreamingMode.NONE
            ),
        )
    ) as events:
        return [event async for event in events]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_native_hierarchy_and_independent_calls(native, stream):
    def handler(request):
        body = response([{"text": "hello"}])
        if "streamGenerateContent" in request.url.path:
            return httpx.Response(
                200,
                text="data: " + json.dumps(body) + "\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=body)

    runner, client = make_runner(handler)
    try:
        await asyncio.gather(
            run(runner, "session-a", stream), run(runner, "session-b", stream)
        )
        captured = spans(native[1])
        inference = [
            s
            for s in captured
            if s.attributes.get("gen_ai.operation.name") == "generate_content"
        ]
        assert len(inference) == 2
        assert {s.attributes["gen_ai.conversation.id"] for s in inference} == {
            "session-a",
            "session-b",
        }
        assert all(
            s.instrumentation_scope.name == "gcp.vertex.agent" for s in inference
        )
        agents = [
            s
            for s in captured
            if s.attributes.get("gen_ai.operation.name") == "invoke_agent"
        ]
        assert len(agents) == 2
        assert len({s.context.trace_id for s in agents}) == 2
        ids = {s.context.span_id for s in captured}
        assert all(s.parent is None or s.parent.span_id in ids for s in captured)
        assert any("gcp.vertex.agent.llm_request" in s.attributes for s in captured)
        assert tuple(captured) == native[2].get_finished_spans()
        await client.aio.models.generate_content(
            model="gemini-2.5-flash", contents="direct"
        )
        assert (
            len(
                [
                    s
                    for s in spans(native[1])
                    if s.instrumentation_scope.name == "confident_trace"
                ]
            )
            == 1
        )
        assert not trace.get_current_span().get_span_context().is_valid
    finally:
        await client.aio.aclose()


@pytest.mark.asyncio
async def test_parallel_tools_keep_their_provider_calls(native):
    async def lookup(city: str) -> str:
        """Look up a city."""
        await client.aio.models.generate_content(
            model="gemini-2.5-flash", contents="inside-tool"
        )
        return city

    def handler(request):
        body = json.loads(request.content)
        text = json.dumps(body.get("contents", []))
        if "inside-tool" in text or "functionResponse" in text:
            return httpx.Response(200, json=response([{"text": "done"}]))
        return httpx.Response(
            200,
            json=response(
                [
                    {"functionCall": {"name": "lookup", "args": {"city": "Macau"}}},
                    {"functionCall": {"name": "lookup", "args": {"city": "Tokyo"}}},
                ]
            ),
        )

    runner, client = make_runner(handler, [lookup])
    try:
        await run(runner)
        captured = spans(native[1])
        tools = [
            s
            for s in captured
            if s.attributes.get("gen_ai.operation.name") == "execute_tool"
        ]
        assert (
            len([s for s in tools if s.attributes.get("gen_ai.tool.name") == "lookup"])
            == 2
        )
        own = [s for s in captured if s.instrumentation_scope.name == "confident_trace"]
        assert len(own) == 2
        assert {s.parent.span_id for s in own} <= {s.context.span_id for s in tools}
        assert (
            len(
                [
                    s
                    for s in captured
                    if s.attributes.get("gen_ai.operation.name") == "generate_content"
                ]
            )
            == 4
        )
    finally:
        await client.aio.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["error", "cancel"])
async def test_failed_model_does_not_leak_context(native, mode):
    started = asyncio.Event()

    async def handler(request):
        started.set()
        if mode == "cancel":
            await asyncio.Event().wait()
        raise ValueError("offline failure")

    runner, client = make_runner(handler)
    try:
        task = asyncio.create_task(run(runner))
        await asyncio.wait_for(started.wait(), 10)
        if mode == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if mode == "cancel" else ValueError):
            await task
        assert not trace.get_current_span().get_span_context().is_valid
        assert spans(native[1])
        assert not any(
            s.instrumentation_scope.name == "confident_trace" for s in spans(native[1])
        )
    finally:
        await client.aio.aclose()


def test_instrument_cleanup_and_unrelated_provider(native):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from confident_trace._core import runtime
    from confident_trace.integrations import registry

    provider, exporter, witness = native
    rt = runtime.current()
    assert registry.instrument(rt, ("google_adk",)) == []
    assert ct.init() is rt
    ct.shutdown()
    other = InMemorySpanExporter()
    ct.init(tracer_provider=TracerProvider(), exporter=other)
    with provider.get_tracer("gcp.vertex.agent", "2.8.0").start_as_current_span(
        "generate_content", attributes={"gen_ai.operation.name": "generate_content"}
    ):
        from confident_trace.integrations._shared.lifecycle import (
            native_inference_active,
        )

        assert not native_inference_active(runtime.current())
    assert not other.get_finished_spans()
    assert witness.get_finished_spans()  # Application exporter survives shutdown.


@pytest.mark.asyncio
async def test_early_close_preserves_native_content_policy(native, monkeypatch):
    # Confident's content policy does not rewrite ADK's native attributes.
    ct.shutdown()
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, capture_content=False)
    monkeypatch.setenv("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "false")
    runner, client = make_runner(
        lambda request: httpx.Response(200, json=response([{"text": "hello"}]))
    )
    try:
        await runner.session_service.create_session(
            app_name="native-test", user_id="user", session_id="early"
        )
        async with aclosing(
            runner.run_async(
                user_id="user",
                session_id="early",
                new_message=types.Content(role="user", parts=[types.Part(text="hi")]),
            )
        ) as events:
            await anext(events)
        captured = spans(exporter)
        assert captured
        assert all(
            s.attributes.get("gcp.vertex.agent.llm_request", "{}") == "{}"
            for s in captured
        )
        assert not trace.get_current_span().get_span_context().is_valid
    finally:
        await client.aio.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_version", ["1.0.0", "999.0.0", ""])
async def test_scope_version_does_not_disable_suppression(native, scope_version):
    runner, client = make_runner(
        lambda request: httpx.Response(200, json=response([{"text": "hello"}]))
    )
    try:
        with (
            native[0]
            .get_tracer("gcp.vertex.agent", scope_version)
            .start_as_current_span(
                "native-model", attributes={"gen_ai.operation.name": "generate_content"}
            )
        ):
            await client.aio.models.generate_content(
                model="gemini-2.5-flash", contents="covered"
            )
        # A tool span in the same scope must not suppress its own SDK call.
        with (
            native[0]
            .get_tracer("gcp.vertex.agent", scope_version)
            .start_as_current_span(
                "native-tool", attributes={"gen_ai.operation.name": "execute_tool"}
            )
        ):
            await client.aio.models.generate_content(
                model="gemini-2.5-flash", contents="tool-call"
            )
        captured = spans(native[1])
        assert (
            len(
                [
                    s
                    for s in captured
                    if s.instrumentation_scope.name == "confident_trace"
                ]
            )
            == 1
        )
        assert (
            next(
                s for s in captured if s.name == "native-model"
            ).instrumentation_scope.version
            == scope_version
        )
    finally:
        await client.aio.aclose()
