import asyncio
import json
import os

import httpx
import pytest
from conftest import spans
from openai import APIConnectionError, AsyncOpenAI
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace._core import runtime
from confident_trace.integrations._shared.lifecycle import native_inference_active

FRAMEWORK = os.environ["CT_TEST_FRAMEWORK"]
SCOPE = "pydantic-ai" if FRAMEWORK == "pydantic_ai" else "strands.telemetry.tracer"


@pytest.fixture
def native_integrations():
    return (FRAMEWORK, "openai")


def agent_for(client, tools=()):
    if FRAMEWORK == "pydantic_ai":
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        return Agent(
            OpenAIChatModel("gpt-test", provider=OpenAIProvider(openai_client=client)),
            tools=tools,
        )
    from strands import Agent, tool
    from strands.models.openai import OpenAIModel

    return Agent(
        model=OpenAIModel(client=client, model_id="gpt-test"),
        tools=[tool(t) for t in tools],
        callback_handler=None,
    )


async def run(agent, stream=False):
    if FRAMEWORK == "pydantic_ai":
        if stream:
            async with agent.run_stream("hello") as result:
                return "".join([text async for text in result.stream_text(delta=True)])
        return (await agent.run("hello")).output
    if stream:
        return "".join(
            [
                event["data"]
                async for event in agent.stream_async("hello")
                if "data" in event
            ]
        )
    return "".join(
        block.get("text", "")
        for block in (await agent.invoke_async("hello")).message["content"]
    )


def client_for(handler):
    return AsyncOpenAI(
        api_key="offline",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def completion(request):
    body = json.loads(request.content)
    use_tool = body.get("tools") and not any(
        m["role"] == "tool" for m in body["messages"]
    )
    message = {"role": "assistant", "content": None if use_tool else "hello"}
    if use_tool:
        message["tool_calls"] = [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }
        ]
    choice = {
        "index": 0,
        "message": message,
        "finish_reason": "tool_calls" if use_tool else "stop",
    }
    body = {
        "id": "chat-1",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-test",
        "choices": [choice],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }
    if not json.loads(request.content).get("stream"):
        return httpx.Response(200, json=body)
    delta = dict(message)
    if use_tool:
        delta["tool_calls"] = [{"index": 0, **message["tool_calls"][0]}]
    chunks = [
        {
            **body,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            "usage": None,
        },
        {
            **body,
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}
            ],
        },
    ]
    return httpx.Response(
        200,
        text="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        + "data: [DONE]\n\n",
        headers={"content-type": "text/event-stream"},
    )


def assert_tree(captured):
    (root,) = [s for s in captured if s.name == "request"]
    by_id = {s.context.span_id: s for s in captured}
    assert root.parent is None
    assert {s.context.trace_id for s in captured} == {root.context.trace_id}
    for span in captured:
        seen = set()
        while span is not root:
            assert span.context.span_id not in seen
            seen.add(span.context.span_id)
            assert span.parent is not None
            span = by_id[span.parent.span_id]
    return root


def test_init_after_agent_creation():
    # Construct against OTel's proxy before init; Strands caches its tracer.
    client = client_for(completion)
    agent = agent_for(client)
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter)
    try:
        with ct.span("request"):
            if FRAMEWORK == "pydantic_ai":
                assert agent.run_sync("hello").output == "hello"
            else:
                assert agent("hello").message["content"] == [{"text": "hello"}]
        captured = spans(exporter)
        assert_tree(captured)
        (chat,) = [
            s for s in captured if s.attributes.get("gen_ai.operation.name") == "chat"
        ]
        assert chat.instrumentation_scope.name == SCOPE
    finally:
        asyncio.run(client.close())
        ct.shutdown()


@pytest.mark.parametrize("stream", [False, True])
async def test_agents_streams_tools_and_direct_calls(native, stream):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return completion(request)

    async with client_for(handler) as client:

        async def lookup() -> str:
            """Look up the answer."""
            result = await client.chat.completions.create(
                model="gpt-test",
                messages=[{"role": "user", "content": "tool provider call"}],
            )
            return result.choices[0].message.content

        agent = agent_for(client, [lookup])
        with ct.span("request", thread_id="session-1"):
            assert await run(agent, stream) == "hello"
            assert (
                await client.chat.completions.create(
                    model="gpt-test", messages=[{"role": "user", "content": "direct"}]
                )
            ).choices[0].message.content == "hello"
    captured = spans(native[1])
    assert tuple(captured) == native[2].get_finished_spans()
    root = assert_tree(captured)
    assert root.attributes["gen_ai.conversation.id"] == "session-1"
    native_spans = [s for s in captured if s.instrumentation_scope.name == SCOPE]
    chats = [
        s for s in native_spans if s.attributes.get("gen_ai.operation.name") == "chat"
    ]
    assert len(chats) == 2
    assert all(s.attributes["gen_ai.usage.input_tokens"] == 2 for s in chats)
    (tool_span,) = [
        s
        for s in native_spans
        if s.attributes.get("gen_ai.operation.name") == "execute_tool"
    ]
    provider_spans = [
        s
        for s in captured
        if s.instrumentation_scope.name == "confident_trace"
        and s.attributes.get("gen_ai.operation.name") == "chat"
    ]
    assert len(provider_spans) == 2
    assert {s.parent.span_id for s in provider_spans} == {
        root.context.span_id,
        tool_span.context.span_id,
    }
    assert len(calls) == 4


async def test_concurrent_parentage(native):
    async with client_for(completion) as client:

        async def invoke():
            with ct.span("request"):
                assert await run(agent_for(client)) == "hello"

        await asyncio.gather(invoke(), invoke())
    captured = spans(native[1])
    trace_ids = {s.context.trace_id for s in captured}
    assert len(trace_ids) == 2
    for trace_id in trace_ids:
        assert_tree([s for s in captured if s.context.trace_id == trace_id])


class Started(SpanProcessor):
    def __init__(self):
        self.ids = set()

    def on_start(self, span, parent_context=None):
        self.ids.add(span.context.span_id)


@pytest.mark.parametrize("cancel", [False, True])
async def test_failure_and_cancellation(native, cancel):
    started = Started()
    native[0].add_span_processor(started)
    entered = asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        raise httpx.ConnectError("offline", request=request)

    async with client_for(handler) as client:
        task = asyncio.create_task(run(agent_for(client)))
        if cancel:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
        failure = APIConnectionError
        if FRAMEWORK == "pydantic_ai":
            from pydantic_ai.exceptions import ModelAPIError

            failure = ModelAPIError
        with pytest.raises(asyncio.CancelledError if cancel else failure) as caught:
            await task
        if not cancel:
            error = caught.value
            if FRAMEWORK == "pydantic_ai":
                error = error.__cause__
            assert isinstance(error, APIConnectionError)
            assert isinstance(error.__cause__, httpx.ConnectError)
    captured = spans(native[1])
    assert len(calls) == 1
    if FRAMEWORK == "strands" and cancel and not captured:
        # 1.54.0 uses except Exception with end_on_exit=False. Cancellation
        # bypasses agent, cycle and model span cleanup (not just error status).
        assert len(started.ids) == 3
        assert native[2].get_finished_spans() == ()
        pytest.xfail("Strands native cancellation leaks agent/cycle/model spans")
    assert started.ids == {s.context.span_id for s in captured}
    chats = [s for s in captured if s.attributes.get("gen_ai.operation.name") == "chat"]
    assert len(chats) == 1 and chats[0].instrumentation_scope.name == SCOPE
    agents = [
        s
        for s in captured
        if s.attributes.get("gen_ai.operation.name") == "invoke_agent"
    ]
    assert len(agents) == 1
    if not cancel:
        assert chats[0].status.status_code.name == "ERROR"
        assert agents[0].status.status_code.name == "ERROR"
    assert tuple(captured) == native[2].get_finished_spans()


async def test_shutdown_reinit_and_provider_identity(native):
    rt = runtime.current()
    other = TracerProvider(shutdown_on_exit=False)
    for provider, expected in [(native[0], True), (other, False)]:
        with provider.get_tracer(SCOPE).start_as_current_span(
            "chat", attributes={"gen_ai.operation.name": "chat"}
        ):
            assert native_inference_active(rt) is expected
    ct.shutdown()
    with (
        native[0]
        .get_tracer(SCOPE)
        .start_as_current_span("chat", attributes={"gen_ai.operation.name": "chat"})
    ):
        assert not native_inference_active(rt)
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=(FRAMEWORK, "openai"))
    async with client_for(completion) as client:
        assert await run(agent_for(client)) == "hello"
    captured = spans(exporter)
    assert (
        len(
            [s for s in captured if s.attributes.get("gen_ai.operation.name") == "chat"]
        )
        == 1
    )
    other.shutdown()


async def test_pydantic_existing_settings_and_optout(native, monkeypatch):
    if FRAMEWORK != "pydantic_ai":
        pytest.skip("Pydantic-specific configuration")
    from pydantic_ai import Agent
    from pydantic_ai.models.instrumented import InstrumentationSettings

    other = TracerProvider(shutdown_on_exit=False)
    witness = InMemorySpanExporter()
    other.add_span_processor(SimpleSpanProcessor(witness))
    settings = InstrumentationSettings(tracer_provider=other, include_content=False)
    monkeypatch.setattr(Agent, "_instrument_default", settings)
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=(FRAMEWORK, "openai"))
    assert Agent._instrument_default is settings
    async with client_for(completion) as client:
        assert await run(agent_for(client)) == "hello"
        agent = agent_for(client)
        agent.instrument = False
        assert await run(agent) == "hello"
    captured = spans(exporter)
    assert len(captured) == 2
    assert all(s.instrumentation_scope.name == "confident_trace" for s in captured)
    foreign_chats = [
        s
        for s in witness.get_finished_spans()
        if s.attributes.get("gen_ai.operation.name") == "chat"
    ]
    assert len(foreign_chats) == 1
    assert "hello" not in foreign_chats[0].attributes.get("gen_ai.input.messages", "")
    other.shutdown()


async def test_early_stream_close(native):
    started = Started()
    native[0].add_span_processor(started)
    async with client_for(completion) as client:
        agent = agent_for(client)
        with ct.span("request"):
            if FRAMEWORK == "pydantic_ai":
                async with agent.run_stream("hello") as result:
                    iterator = result.stream_text(delta=True)
                    assert await anext(iterator) == "hello"
                    await iterator.aclose()
            else:
                iterator = agent.stream_async("hello")
                async for event in iterator:
                    if "data" in event:
                        assert event["data"] == "hello"
                        break
                await iterator.aclose()
    captured = spans(native[1])
    if FRAMEWORK == "strands" and len(captured) == 1:
        assert captured[0].name == "request"
        assert len(started.ids) == 4
        pytest.xfail("Strands native early stream close leaks agent/cycle/model spans")
    assert started.ids == {s.context.span_id for s in captured}
    assert_tree(captured)


def test_sdk_disabled_does_not_enable(monkeypatch):
    ct.shutdown()
    if FRAMEWORK == "pydantic_ai":
        from pydantic_ai import Agent

        monkeypatch.setattr(Agent, "_instrument_default", False)

        def unexpected(*args, **kwargs):
            pytest.fail("OTEL_SDK_DISABLED must prevent native enablement")

        monkeypatch.setattr(Agent, "instrument_all", unexpected)
    excluded = ct.init(exporter=InMemorySpanExporter(), instrumentations=())
    assert excluded.active
    ct.shutdown()
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    rt = ct.init(exporter=InMemorySpanExporter())
    assert not rt.active
    assert not native_inference_active(rt)


async def test_explicit_shared_provider(native):
    if FRAMEWORK != "pydantic_ai":
        pytest.skip("Strands uses the global provider")
    from pydantic_ai.models.instrumented import InstrumentationSettings

    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=provider,
        exporter=exporter,
        instrumentations=(FRAMEWORK, "openai"),
    )
    async with client_for(completion) as client:
        agent = agent_for(client)
        agent.instrument = InstrumentationSettings(tracer_provider=provider)
        with ct.span("request"):
            assert await run(agent) == "hello"
    captured = spans(exporter)
    assert_tree(captured)
    assert (
        len(
            [s for s in captured if s.attributes.get("gen_ai.operation.name") == "chat"]
        )
        == 1
    )
    provider.shutdown()


async def test_native_content_is_not_rewritten(native):
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        exporter=exporter, capture_content=False, instrumentations=(FRAMEWORK, "openai")
    )
    async with client_for(completion) as client:
        assert await run(agent_for(client)) == "hello"
    captured = spans(exporter)
    assert tuple(captured) == native[2].get_finished_spans()
    (chat,) = [
        s for s in captured if s.attributes.get("gen_ai.operation.name") == "chat"
    ]
    # Pydantic uses message attributes, Strands' default uses native events.
    native_content = str(dict(chat.attributes)) + str(
        [dict(e.attributes) for e in chat.events]
    )
    assert "hello" in native_content
