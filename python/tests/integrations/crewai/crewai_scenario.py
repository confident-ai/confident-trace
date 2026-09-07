"""Offline CrewAI executions; failures point to this file, not a -c string."""

import asyncio
import contextvars
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from conftest import spans
from crewai_core.token_manager import TokenManager
from openai import AsyncOpenAI, OpenAI
from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace._core import runtime
from confident_trace._semconv import genai_v1_37_0 as ai
from confident_trace.integrations import registry

# CrewAI creates credential storage even with tracing disabled. Isolate it before
# importing the framework; never read or modify the developer's credentials.
with patch.object(
    TokenManager,
    "_get_secure_storage_path",
    return_value=Path(os.environ["CREWAI_STORAGE_DIR"]),
):
    from crewai import LLM, Agent, Crew, Task
    from crewai.flow.flow import Flow, listen, start
    from crewai.tools import tool


class Witness(SpanProcessor):
    def __init__(self):
        self.started = []
        self.ended = []

    def on_start(self, span, parent_context=None):
        self.started.append(span.context.span_id)

    def on_end(self, span):
        self.ended.append(span.context.span_id)


provider = TracerProvider(shutdown_on_exit=False)
exporter = InMemorySpanExporter()
witness = Witness()
external = InMemorySpanExporter()
provider.add_span_processor(witness)
provider.add_span_processor(SimpleSpanProcessor(external))
original = Crew.kickoff
mode = sys.argv[1]
if mode == "disabled":
    os.environ["OTEL_SDK_DISABLED"] = "true"
ct.init(
    tracer_provider=provider,
    exporter=exporter,
    instrumentations=("crewai", "openai"),
    capture_content=mode != "content",
)
state = getattr(runtime.current(), "_crewai_state", None)


def llm(*, tools=False, stream=False, fail=False, wait=None):
    calls = []

    def respond(request):
        data = json.loads(request.content)
        calls.append(data)
        if wait:
            wait()
        if fail:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "secret failure",
                        "type": "invalid_request_error",
                    }
                },
            )
        if tools and not any(m["role"] == "tool" for m in data["messages"]):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{i}",
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "arguments": json.dumps({"value": str(i)}),
                        },
                    }
                    for i in range(2)
                ],
            }
            reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": "done"}
            reason = "stop"
        body = {
            "id": "chat-test",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "message": message, "finish_reason": reason}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        }
        if data.get("stream"):
            frames = []
            for delta, finish in [
                ({"role": "assistant", "content": "do"}, None),
                ({"content": "ne"}, None),
                ({}, "stop"),
            ]:
                frames.append(
                    {
                        **body,
                        "object": "chat.completion.chunk",
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": finish}
                        ],
                        "usage": body["usage"] if finish else None,
                    }
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="".join("data: " + json.dumps(f) + "\n\n" for f in frames)
                + "data: [DONE]\n\n",
            )
        return httpx.Response(200, json=body)

    model = LLM(model="openai/gpt-4o-mini", api_key="test", stream=stream)
    model._client = OpenAI(
        api_key="test",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    model._async_client = AsyncOpenAI(
        api_key="test",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    return model, calls


def crew(model, tools=(), *, stream=False, timeout=None, asynchronous=False):
    agent = Agent(
        role="researcher",
        goal="answer",
        backstory="test",
        llm=model,
        tools=list(tools),
        max_retry_limit=0,
        max_execution_time=timeout,
        allow_delegation=False,
    )
    task = Task(
        name="question",
        description="answer the question",
        expected_output="answer",
        agent=agent,
        async_execution=asynchronous,
    )
    return Crew(
        name="research",
        agents=[agent],
        tasks=[task],
        cache=False,
        stream=stream,
        tracing=False,
    )


def check_parent(child, parent):
    assert child.parent.span_id == parent.context.span_id, (child.name, parent.name)
    assert child.context.trace_id == parent.context.trace_id


def hierarchy():
    direct, direct_calls = llm()
    tool_calls = []
    parallel_tools = threading.Barrier(2)

    @tool
    def lookup(value: str) -> str:
        """Look up a value."""
        tool_calls.append(value)
        parallel_tools.wait(10)
        with ct.span("inside tool"):
            with provider.get_tracer("application").start_as_current_span(
                "third-party"
            ):
                assert direct.call("direct") == "done"
        return value

    model, calls = llm(tools=True)
    c = crew(model, [lookup], timeout=5, asynchronous=True)
    with ct.span("request", thread_id="conversation"):
        assert c.kickoff().raw == "done"
    captured = spans(exporter)
    request, crew_span, task_span, agent_span = [
        next(s for s in captured if s.name == n)
        for n in (
            "request",
            "crew research",
            "task question",
            "invoke_agent researcher",
        )
    ]
    check_parent(crew_span, request)
    check_parent(task_span, crew_span)
    check_parent(agent_span, task_span)
    tools = [
        s
        for s in captured
        if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "execute_tool"
    ]
    models = [
        s for s in captured if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "chat"
    ]
    assert len(calls) == len(direct_calls) == len(tools) == 2
    assert sorted(tool_calls) == ["0", "1"]
    assert len(models) == 4
    executor = next(s for s in captured if s.name == "flow AgentExecutor")
    check_parent(executor, agent_span)
    tool_node = next(s for s in captured if s.name == "node execute_native_tool")
    check_parent(tool_node, executor)
    for s in tools:
        check_parent(s, tool_node)
        assert s.status.status_code.name == "UNSET"
    assert {s.attributes[ai.GEN_AI_TOOL_CALL_ID] for s in tools} == {"call-0", "call-1"}
    model_nodes = [s for s in captured if s.name == "node call_llm_native_tools"]
    inference_nodes = {s.context.span_id for s in model_nodes}
    assert len([s for s in models if s.parent.span_id in inference_nodes]) == 2
    for node in model_nodes:
        check_parent(node, executor)
    for s in captured:
        assert s.context.trace_id == request.context.trace_id
        if s.name == "inside tool":
            assert s.parent.span_id in {t.context.span_id for t in tools}
        if s.instrumentation_scope.name == "confident_trace":
            assert s.attributes[ai.GEN_AI_CONVERSATION_ID] == "conversation"
    for s in models:
        assert s.attributes[ai.GEN_AI_USAGE_INPUT_TOKENS] == 11
        assert s.attributes[ai.GEN_AI_USAGE_OUTPUT_TOKENS] == 3
    assert any("tool_call" in s.attributes[ai.GEN_AI_OUTPUT_MESSAGES] for s in models)


async def asynchronous():
    for method in ("akickoff", "kickoff_async"):
        model, calls = llm()
        with ct.span(method):
            assert (await getattr(crew(model), method)()).raw == "done"
        assert len(calls) == 1
    model, calls = llm()
    with ct.span("standalone"):
        assert (
            await Agent(
                role="solo", goal="answer", backstory="test", llm=model
            ).kickoff_async("hello")
        ).raw == "done"
    assert len(calls) == 1
    captured = spans(exporter)
    for name in ("akickoff", "kickoff_async", "standalone"):
        root = next(s for s in captured if s.name == name)
        group = [s for s in captured if s.context.trace_id == root.context.trace_id]
        assert (
            len(
                [
                    s
                    for s in group
                    if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "invoke_agent"
                ]
            )
            == 1
        )
        assert (
            len(
                [
                    s
                    for s in group
                    if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "chat"
                ]
            )
            == 1
        )
    assert not trace.get_current_span().get_span_context().is_valid


def concurrency():
    barrier = threading.Barrier(2)

    def execute(i):
        model, calls = llm(wait=lambda: barrier.wait(10))
        with ct.span(str(i), thread_id="same conversation"):
            assert crew(model).kickoff().raw == "done"
        assert len(calls) == 1
        assert not trace.get_current_span().get_span_context().is_valid

    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(2):
            list(pool.map(execute, range(2)))
        with ct.span("explicit") as root:
            model, calls = llm()
            ctx = contextvars.copy_context()
            assert pool.submit(ctx.run, crew(model).kickoff).result().raw == "done"
    captured = spans(exporter)
    roots = [s for s in captured if s.parent is None]
    assert len(roots) == 5
    assert len({s.context.trace_id for s in roots}) == 5
    for r in roots:
        group = [s for s in captured if s.context.trace_id == r.context.trace_id]
        assert len([s for s in group if s.name == "crew research"]) == 1
        assert (
            len(
                [
                    s
                    for s in group
                    if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "chat"
                ]
            )
            == 1
        )
        ids = {s.context.span_id for s in group}
        assert all(s.parent is None or s.parent.span_id in ids for s in group)
    assert root.get_span_context().is_valid


async def structured():
    from crewai.tools.tool_failure import ToolFailure

    hits = []

    @tool
    def sync(value: str) -> str:
        """Return a value."""
        with ct.span("sync body"):
            hits.append(value)
        return value

    structured_tool = sync.to_structured_tool()
    original_func = structured_tool.func

    async def execute(i):
        with ct.span(str(i)):
            assert await structured_tool.ainvoke({"value": str(i)}) == str(i)

    await asyncio.gather(execute(1), execute(2))
    assert structured_tool.func is original_func
    assert sorted(hits) == ["1", "2"]

    @tool
    async def asynchronous_tool(value: str) -> str:
        """Return asynchronously."""
        await asyncio.sleep(0)
        with ct.span("async body"):
            return value

    with ct.span("async request"):
        assert await asynchronous_tool.arun(value="ok") == "ok"

    @tool
    def failure() -> ToolFailure:
        """Report a failure without raising."""
        return ToolFailure(message="private failure")

    assert isinstance(failure.run(), ToolFailure)
    captured = spans(exporter)
    for s in captured:
        if s.name in ("sync body", "async body"):
            parent = next(p for p in captured if p.context.span_id == s.parent.span_id)
            assert parent.attributes[ai.GEN_AI_OPERATION_NAME] == "execute_tool"
            assert parent.context.trace_id == s.context.trace_id
    failed = next(s for s in captured if s.name == "execute_tool failure")
    assert failed.status.status_code.name == "ERROR"
    assert failed.attributes[ai.ERROR_TYPE] == "ToolFailure"


async def flow():
    class Workflow(Flow):
        @start()
        async def begin(self):
            with ct.span("manual begin"):
                return "hello"

        @listen(begin)
        def left(self, value):
            with provider.get_tracer("application").start_as_current_span(
                "manual left"
            ):
                return value + " left"

        @listen(begin)
        async def right(self, value):
            with ct.span("manual right"):
                await asyncio.sleep(0)
                return value + " right"

    with ct.span("request"):
        result = await Workflow(tracing=False).kickoff_async()
        assert result in ("hello left", "hello right")
    captured = spans(exporter)
    root = next(s for s in captured if s.name == "request")
    flow_span = next(s for s in captured if s.name == "flow Workflow")
    check_parent(flow_span, root)
    for name in ("begin", "left", "right"):
        node = next(s for s in captured if s.name == "node " + name)
        check_parent(node, flow_span)
        check_parent(next(s for s in captured if s.name == "manual " + name), node)
        assert ai.GEN_AI_OPERATION_NAME not in node.attributes


def stream():
    model, calls = llm(stream=True)
    c = crew(model, stream=True)
    handle = c.kickoff()
    assert not spans(exporter)
    with ct.span("consumer") as root:
        chunks = []
        for chunk in handle:
            chunks.append(chunk.content)
            assert trace.get_current_span() is root
        assert handle.result.raw == "done"
    assert "".join(chunks) == "done"
    assert len(calls) == 1
    captured = spans(exporter)
    assert len([s for s in captured if s.name == "crew research"]) == 1
    model_span = next(
        s for s in captured if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "chat"
    )
    assert model_span.attributes[ai.GEN_AI_USAGE_OUTPUT_TOKENS] == 3
    assert "done" in model_span.attributes[ai.GEN_AI_OUTPUT_MESSAGES]
    assert len({s.context.trace_id for s in captured}) == 1


async def termination():
    from openai import BadRequestError

    model, calls = llm(fail=True)
    with pytest.raises(BadRequestError):
        await crew(model).akickoff()
    assert len(calls) == 1
    captured = spans(exporter)
    failed = [
        s
        for s in captured
        if s.name
        in (
            "crew research",
            "task question",
            "invoke_agent researcher",
            "chat gpt-4o-mini",
            "flow AgentExecutor",
            "node call_llm_and_parse",
        )
    ]
    assert len(failed) == 6
    assert all(s.status.status_code.name == "ERROR" for s in failed)
    assert all(
        "secret failure" not in str(s.attributes.get(ai.ERROR_TYPE)) for s in captured
    )
    started = asyncio.Event()

    class Wait(Flow):
        @start()
        async def wait(self):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(Wait(tracing=False).kickoff_async())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    captured = spans(exporter)
    cancelled = [s for s in captured if s.name in ("flow Wait", "node wait")]
    assert len(cancelled) == 2
    assert all(s.status.status_code.name == "ERROR" for s in cancelled)
    assert all(s.attributes[ai.ERROR_TYPE] == "CancelledError" for s in cancelled)
    assert not state.operations


def lifecycle():
    import wrapt

    assert registry.instrument(runtime.current(), ("crewai", "crewai")) == []

    @tool
    def simple() -> str:
        """Return a constant."""
        return "done"

    assert simple.run() == "done"
    assert spans(exporter) == external.get_finished_spans()
    ct.shutdown()
    assert Crew.kickoff is original
    assert simple.run() == "done"
    assert len(external.get_finished_spans()) == 1
    ct.init(
        tracer_provider=provider,
        exporter=InMemorySpanExporter(),
        instrumentations=("crewai",),
    )
    assert runtime.current()._crewai_state is not state
    assert simple.run() == "done"
    assert len(external.get_finished_spans()) == 2
    ours = vars(Crew)["kickoff"]
    later = wrapt.FunctionWrapper(
        ours, lambda wrapped, instance, args, kwargs: wrapped(*args, **kwargs)
    )
    Crew.kickoff = later
    ct.shutdown()
    assert vars(Crew)["kickoff"] is later
    Crew.kickoff = original


def disabled():
    model, calls = llm()
    assert crew(model).kickoff().raw == "done"
    assert len(calls) == 1
    assert not spans(exporter)


def content():
    model, calls = llm()
    assert crew(model).kickoff(inputs={"secret": "private"}).raw == "done"
    captured = spans(exporter)
    assert (
        len(
            [
                s
                for s in captured
                if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "chat"
            ]
        )
        == 1
    )
    assert all(
        not any(k.endswith((".input", ".output", ".messages")) for k in s.attributes)
        for s in captured
    )


async def workers():
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    @tool
    def wait_for_release() -> str:
        """Wait for the test to release this worker."""
        with ct.span("worker body"):
            entered.set()
            assert release.wait(10)
        finished.set()
        return "done"

    structured_tool = wait_for_release.to_structured_tool()
    with ct.span("request") as root:
        task = asyncio.create_task(structured_tool.ainvoke({}))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert trace.get_current_span() is root
        # The callable is still running. Its own manual span must not be ended
        # by cancelling the awaiting task's tool span.
        before = spans(exporter)
        assert not any(s.name == "worker body" for s in before)
        assert (
            next(
                s for s in before if s.name == "execute_tool wait_for_release"
            ).status.status_code.name
            == "ERROR"
        )
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
    captured = spans(exporter)
    body = next(s for s in captured if s.name == "worker body")
    tool_span = next(s for s in captured if s.name == "execute_tool wait_for_release")
    check_parent(body, tool_span)
    # Shutdown with a still-running owned tool ends it once, without stopping it.
    entered.clear()
    release.clear()
    finished.clear()
    worker = asyncio.create_task(structured_tool.ainvoke({}))
    assert await asyncio.to_thread(entered.wait, 5)
    ct.shutdown()
    assert not state.operations
    release.set()
    assert await worker == "done"


async def stream_close():
    for native in (False, True):
        model, calls = llm(stream=True)
        c = crew(model, stream=True)
        with ct.span("async consumer") as root:
            handle = await (c.akickoff() if native else c.kickoff_async())
            chunks = []
            async for chunk in handle:
                chunks.append(chunk.content)
                assert trace.get_current_span() is root
            assert "".join(chunks) == "done"
            assert handle.result.raw == "done"
        assert len(calls) == 1
    model, calls = llm(stream=True)
    with ct.span("early consumer") as root:
        handle = crew(model, stream=True).kickoff()
        iterator = iter(handle)
        assert next(iterator).content == "do"
        assert trace.get_current_span() is root
        handle.close()
        iterator.close()
    assert len(calls) == 1
    captured = spans(exporter)
    assert len([s for s in captured if s.name == "crew research"]) == 3
    assert (
        len(
            [
                s
                for s in captured
                if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "chat"
            ]
        )
        == 3
    )


async def resume():
    from crewai.flow import human_feedback
    from crewai.flow.async_feedback import HumanFeedbackPending

    class Reviewer:
        def request_feedback(self, context, flow):
            raise HumanFeedbackPending(context)

    class Review(Flow):
        @start()
        @human_feedback(message="review", provider=Reviewer())
        def draft(self):
            return "draft"

        @listen(draft)
        def publish(self, value):
            with ct.span("publish body"):
                return "published"

    f = Review(tracing=False)
    with ct.span("first", thread_id="review"):
        pending = await f.kickoff_async()
        assert isinstance(pending, HumanFeedbackPending)
    first = spans(exporter)
    assert (
        next(s for s in first if s.name == "node draft").status.status_code.name
        == "UNSET"
    )
    with ct.span("resumed", thread_id="review"):
        restored = Review.from_pending(pending.context.flow_id, tracing=False)
        assert await restored.resume_async("approved") == "published"
    captured = spans(exporter)
    flows = [s for s in captured if s.name == "flow Review"]
    assert len(flows) == 2
    assert flows[0].context.trace_id != flows[1].context.trace_id
    assert all(s.status.status_code.name == "UNSET" for s in flows)
    node = next(s for s in captured if s.name == "node publish")
    check_parent(next(s for s in captured if s.name == "publish body"), node)
    check_parent(node, flows[1])


def diagnostics():
    import io
    import logging

    from confident_trace.integrations.crewai import extraction

    model, calls = llm()
    log = logging.getLogger("confident_trace")
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        with patch.object(extraction, "request", side_effect=ValueError("SECRET")):
            assert crew(model).kickoff().raw == "done"
        assert len(calls) == 1
        assert "ValueError" in output.getvalue()
        assert "SECRET" not in output.getvalue()
    finally:
        log.removeHandler(handler)
    ct.shutdown()
    limited = InMemorySpanExporter()
    ct.init(
        tracer_provider=provider,
        exporter=limited,
        instrumentations=("crewai",),
        max_content_bytes=100,
        redact=lambda value: "redacted",
    )

    @tool
    def echo(value: str) -> str:
        """Return the value."""
        return value

    assert echo.run(value="SECRET" * 100) == "SECRET" * 100
    captured = spans(limited)
    assert len(captured) == 1
    for key, value in captured[0].attributes.items():
        if key.endswith((".input", ".output")):
            assert value == '"redacted"'


run = {
    "hierarchy": hierarchy,
    "async": asynchronous,
    "concurrency": concurrency,
    "structured": structured,
    "flow": flow,
    "stream": stream,
    "termination": termination,
    "lifecycle": lifecycle,
    "disabled": disabled,
    "content": content,
    "workers": workers,
    "stream-close": stream_close,
    "resume": resume,
    "diagnostics": diagnostics,
}[mode]
try:
    if asyncio.iscoroutinefunction(run):
        asyncio.run(run())
    else:
        run()
    ct.flush()
    assert state is None or not state.operations
    assert sorted(witness.started) == sorted(witness.ended)
    assert len(witness.ended) == len(set(witness.ended))
finally:
    ct.shutdown()
