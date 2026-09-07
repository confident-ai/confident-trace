import asyncio
import json

import httpx
import pytest
from agents import (
    Agent,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
    function_tool,
    set_trace_processors,
)
from agents import trace as agent_trace
from agents.tracing import TracingProcessor, get_trace_provider
from conftest import spans
from openai import APIConnectionError, AsyncOpenAI
from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace._core import runtime
from confident_trace.integrations._shared.lifecycle import native_inference_active

SCOPE = "openinference.instrumentation.openai_agents"


class Witness(TracingProcessor):
    def __init__(self):
        self.started = set()
        self.ended = set()

    def on_trace_start(self, trace):
        self.started.add(trace.trace_id)

    def on_trace_end(self, trace):
        self.ended.add(trace.trace_id)

    def on_span_start(self, span):
        self.started.add(span.span_id)

    def on_span_end(self, span):
        self.ended.add(span.span_id)

    def force_flush(self):
        pass

    def shutdown(self):
        pass


# Remove only the default remote exporter in this isolated offline test process.
witness = Witness()
set_trace_processors([witness])


@pytest.fixture
def native_integrations():
    return ("openai_agents", "openai")


def completion(request):
    body = json.loads(request.content)
    use_tool = body.get("tools") and not any(
        m["role"] == "tool" for m in body["messages"]
    )
    message = {"role": "assistant", "content": None if use_tool else "hello"}
    if use_tool:
        name = body["tools"][0]["function"]["name"]
        message["tool_calls"] = [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
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


def client_for(handler=completion):
    return AsyncOpenAI(
        api_key="offline",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def agent_for(client, **kwargs):
    return Agent(
        name="assistant",
        model=OpenAIChatCompletionsModel(model="gpt-test", openai_client=client),
        **kwargs,
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


@pytest.mark.parametrize("stream", [False, True])
async def test_tools_streams_and_direct_calls(native, stream):
    calls = []

    def handler(request):
        calls.append(request)
        return completion(request)

    before = set(witness.started)
    async with client_for(handler) as client:

        @function_tool
        async def lookup() -> str:
            """Look up the answer."""
            with ct.span("tool-step"):
                result = await client.chat.completions.create(
                    model="gpt-test",
                    messages=[{"role": "user", "content": "tool call"}],
                )
                return result.choices[0].message.content

        agent = agent_for(client, tools=[lookup])
        with ct.span("request", thread_id="session-1") as root:
            if stream:
                result = Runner.run_streamed(agent, "hello")
                text = ""
                async for event in result.stream_events():
                    if (
                        event.type == "raw_response_event"
                        and event.data.type == "response.output_text.delta"
                    ):
                        text += event.data.delta
                assert text == "hello"
            else:
                result = await Runner.run(agent, "hello")
            assert result.final_output == "hello"
            assert trace.get_current_span() is root
            await client.chat.completions.create(
                model="gpt-test", messages=[{"role": "user", "content": "direct"}]
            )
        assert not trace.get_current_span().get_span_context().is_valid
    captured = spans(native[1])
    assert tuple(captured) == native[2].get_finished_spans()
    root = assert_tree(captured)
    llms = [s for s in captured if s.attributes.get("openinference.span.kind") == "LLM"]
    assert len(llms) == 2
    assert all(s.attributes["llm.token_count.prompt"] == 2 for s in llms)
    provider_spans = [
        s for s in captured if s.attributes.get("gen_ai.operation.name") == "chat"
    ]
    assert len(provider_spans) == 2
    (step,) = [s for s in captured if s.name == "tool-step"]
    (tool,) = [
        s for s in captured if s.attributes.get("openinference.span.kind") == "TOOL"
    ]
    assert step.parent.span_id == tool.context.span_id
    assert {s.parent.span_id for s in provider_spans} == {
        step.context.span_id,
        root.context.span_id,
    }
    assert len(calls) == 4
    assert witness.started - before
    assert witness.started == witness.ended


async def test_handoff_and_concurrent_requests(native):
    async with client_for() as client:
        target = agent_for(client)
        source = Agent(name="router", model=target.model, handoffs=[target])

        async def invoke(agent):
            with ct.span("request"):
                result = await Runner.run(agent, "hello")
                assert result.final_output == "hello"
                assert result.last_agent is target

        await asyncio.gather(invoke(source), invoke(target))
    captured = spans(native[1])
    trace_ids = {s.context.trace_id for s in captured}
    assert len(trace_ids) == 2
    for trace_id in trace_ids:
        assert_tree([s for s in captured if s.context.trace_id == trace_id])
    assert len([s for s in captured if s.name == "handoff to assistant"]) == 1


class Started(SpanProcessor):
    def __init__(self):
        self.ids = set()

    def on_start(self, span, parent_context=None):
        self.ids.add(span.context.span_id)


@pytest.mark.parametrize("cancel", [False, True])
async def test_failure_and_cancellation(native, cancel):
    entered = asyncio.Event()
    calls = []
    started = Started()
    native[0].add_span_processor(started)

    async def handler(request):
        calls.append(request)
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        raise httpx.ConnectError("offline", request=request)

    async with client_for(handler) as client:
        task = asyncio.create_task(Runner.run(agent_for(client), "hello"))
        if cancel:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else APIConnectionError):
            await task
    captured = spans(native[1])
    assert len(calls) == 1
    assert started.ids == {s.context.span_id for s in captured}
    (llm,) = [
        s for s in captured if s.attributes.get("openinference.span.kind") == "LLM"
    ]
    if not cancel:
        assert llm.status.status_code.name == "ERROR"
    assert not trace.get_current_span().get_span_context().is_valid


async def test_shutdown_reinit_and_unrelated_provider(native):
    rt = runtime.current()
    other = TracerProvider(shutdown_on_exit=False)
    with other.get_tracer(SCOPE).start_as_current_span(
        "llm", attributes={"openinference.span.kind": "LLM"}
    ):
        assert not native_inference_active(rt)
    processors = get_trace_provider()._multi_processor._processors
    ct.shutdown()
    async with client_for() as client:
        assert (await Runner.run(agent_for(client), "hello")).final_output == "hello"
    assert (
        len(
            [
                s
                for s in native[2].get_finished_spans()
                if s.attributes.get("openinference.span.kind") == "LLM"
            ]
        )
        == 1
    )
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("openai_agents", "openai"))
    assert get_trace_provider()._multi_processor._processors == processors
    async with client_for() as client:
        assert (await Runner.run(agent_for(client), "hello")).final_output == "hello"
    captured = spans(exporter)
    assert (
        len(
            [
                s
                for s in captured
                if s.attributes.get("openinference.span.kind") == "LLM"
            ]
        )
        == 1
    )
    assert not [
        s for s in captured if s.instrumentation_scope.name == "confident_trace"
    ]
    other.shutdown()


async def test_native_disabled_and_content_policy(native):
    async with client_for() as client:
        result = await Runner.run(
            agent_for(client), "hello", run_config=RunConfig(tracing_disabled=True)
        )
        assert result.final_output == "hello"
    captured = spans(native[1])
    assert (
        len(captured) == 1
        and captured[0].instrumentation_scope.name == "confident_trace"
    )


def test_sync_and_explicit_workflow(native):
    client = client_for()
    try:
        with ct.span("request"):
            with agent_trace("workflow", group_id="group-1"):
                assert (
                    Runner.run_sync(agent_for(client), "hello").final_output == "hello"
                )
        captured = spans(native[1])
        assert_tree(captured)
        assert (
            len(
                [
                    s
                    for s in captured
                    if s.attributes.get("openinference.span.kind") == "LLM"
                ]
            )
            == 1
        )
        assert not trace.get_current_span().get_span_context().is_valid
    finally:
        asyncio.run(client.close())


async def test_stream_cancellation_drains_and_closes(native):
    entered = asyncio.Event()
    exited = asyncio.Event()
    started = Started()
    native[0].add_span_processor(started)

    async def handler(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    async with client_for(handler) as client:
        with ct.span("request") as root:
            result = Runner.run_streamed(agent_for(client), "hello")
            await asyncio.wait_for(entered.wait(), 5)
            result.cancel()
            async for _ in result.stream_events():
                pass
            await asyncio.wait_for(exited.wait(), 5)
            assert result.is_complete
            assert trace.get_current_span() is root
    captured = spans(native[1])
    assert started.ids == {s.context.span_id for s in captured}
    assert_tree(captured)


async def test_preserves_preexisting_bridge_settings_and_processors(native):
    from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

    bridge = OpenAIAgentsInstrumentor()
    assert bridge.is_instrumented_by_opentelemetry
    processors = get_trace_provider()._multi_processor._processors
    ct.shutdown()
    exporter = InMemorySpanExporter()
    ct.init(
        exporter=exporter,
        capture_content=False,
        instrumentations=("openai_agents", "openai"),
    )
    assert get_trace_provider()._multi_processor._processors == processors
    async with client_for() as client:
        await Runner.run(agent_for(client), "hello")
    captured = spans(exporter)
    (llm,) = [
        s for s in captured if s.attributes.get("openinference.span.kind") == "LLM"
    ]
    assert "hello" in str(dict(llm.attributes))


def test_sdk_disabled_does_not_enable_bridge(monkeypatch):
    from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

    ct.shutdown()

    def unexpected(*args, **kwargs):
        pytest.fail("disabled tracing must not configure the bridge")

    monkeypatch.setattr(OpenAIAgentsInstrumentor, "instrument", unexpected)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert not ct.init(
        exporter=InMemorySpanExporter(), instrumentations=("openai_agents",)
    ).active


@pytest.mark.parametrize("stream", [False, True])
async def test_responses_model(native, stream):
    from agents import OpenAIResponsesModel

    def handler(request):
        response = {
            "id": "resp-1",
            "object": "response",
            "created_at": 1,
            "model": "gpt-test",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "id": "msg-1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "hello", "annotations": []}
                    ],
                }
            ],
            "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        }
        if json.loads(request.content).get("stream"):
            events = [
                {
                    "type": "response.created",
                    "response": {**response, "status": "in_progress", "output": []},
                },
                {
                    "type": "response.output_text.delta",
                    "delta": "hello",
                    "item_id": "msg-1",
                    "output_index": 0,
                    "content_index": 0,
                },
                {"type": "response.completed", "response": response},
            ]
            return httpx.Response(
                200,
                text="".join("data: " + json.dumps(event) + "\n\n" for event in events),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=response)

    async with client_for(handler) as client:
        agent = Agent(
            name="assistant",
            model=OpenAIResponsesModel(model="gpt-test", openai_client=client),
        )
        with ct.span("request"):
            if stream:
                result = Runner.run_streamed(agent, "hello")
                output = ""
                async for event in result.stream_events():
                    if (
                        event.type == "raw_response_event"
                        and event.data.type == "response.output_text.delta"
                    ):
                        output += event.data.delta
                assert output == "hello"
            else:
                result = await Runner.run(agent, "hello")
            assert result.final_output == "hello"
    captured = spans(native[1])
    assert_tree(captured)
    (llm,) = [
        s for s in captured if s.attributes.get("openinference.span.kind") == "LLM"
    ]
    assert llm.attributes["llm.token_count.prompt"] == 2
    assert "hello" in str(dict(llm.attributes))
    assert (
        len([s for s in captured if s.instrumentation_scope.name == "confident_trace"])
        == 1
    )


async def test_guardrail_tripwire(native):
    from agents import (
        GuardrailFunctionOutput,
        InputGuardrailTripwireTriggered,
        input_guardrail,
    )

    @input_guardrail(run_in_parallel=False)
    async def reject(context, agent, input):
        return GuardrailFunctionOutput(output_info="blocked", tripwire_triggered=True)

    calls = []

    def handler(request):
        calls.append(request)
        return completion(request)

    async with client_for(handler) as client:
        with pytest.raises(InputGuardrailTripwireTriggered):
            with ct.span("request"):
                await Runner.run(agent_for(client, input_guardrails=[reject]), "hello")
    captured = spans(native[1])
    assert_tree(captured)
    (guardrail,) = [
        s
        for s in captured
        if s.attributes.get("openinference.span.kind") == "GUARDRAIL"
    ]
    assert guardrail.name == "reject"
    assert calls == []


def test_only_verified_native_kind_suppresses(native):
    rt = runtime.current()
    for attributes, expected in [
        ({"openinference.span.kind": "LLM"}, True),
        ({"openinference.span.kind": "TOOL"}, False),
        ({"gen_ai.operation.name": "chat"}, False),
    ]:
        with (
            native[0]
            .get_tracer(SCOPE)
            .start_as_current_span("native", attributes=attributes)
        ):
            assert native_inference_active(rt) is expected
