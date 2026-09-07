"""Agno agents, tools and workflow execution through actual SDK dispatch."""

import asyncio
import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.team import Team
from agno.tools.function import Function, FunctionCall
from agno.workflow import Workflow
from agno.workflow.parallel import Parallel
from agno.workflow.step import Step
from agno.workflow.types import StepOutput
from support import (
    balanced,
    children,
    client,
    current,
    direct_call,
    init,
    mode,
    operation,
    provider,
)

import confident_trace as ct

original = Agent.run
rt = init("agno")


def model(**kwargs):
    sync, asynchronous, calls = client(**kwargs)
    return OpenAIChat(id="gpt-4o-mini", client=sync, async_client=asynchronous), calls


def lookup(value: str) -> str:
    """Look up a value.

    Args:
        value: The value to look up.
    """
    with ct.span("inside-tool"):
        direct_call()
        with provider.get_tracer("application").start_as_current_span("third-party"):
            return value


def run(name="request", **kwargs):
    m, calls = model(**kwargs)
    agent = Agent(name="assistant", model=m, tools=[lookup], telemetry=False)
    with ct.span(name):
        result = agent.run("question", session_id="conversation")
    assert result.content == "done"
    return calls


if mode in ("hierarchy", "ownership", "content", "disabled"):
    calls = run(tool="lookup")
    assert len(calls) == 2
    result = balanced()
    if mode == "disabled":
        assert not result
    else:
        root = next(s for s in result if s.name == "request")
        (agent,) = operation(result, "invoke_agent")
        (tool,) = operation(result, "execute_tool")
        models = operation(result, "chat")
        assert len(models) == len(calls) + (mode == "ownership")
        models = [s for s in models if s.parent.span_id == agent.context.span_id]
        assert len(models) == len(calls)
        assert agent.parent.span_id == root.context.span_id
        assert tool.parent.span_id == agent.context.span_id
        assert all(s.parent.span_id == agent.context.span_id for s in models)
        assert tool.attributes["gen_ai.tool.call.id"] == "call-lookup"
        assert agent.attributes["gen_ai.conversation.id"] == "conversation"
        assert [s.name for s in children(result, tool)] == ["inside-tool"]
        assert {s.context.trace_id for s in result} == {root.context.trace_id}
        if mode == "content":
            assert not any("hello" in str(dict(s.attributes)) for s in result)
    if mode == "hierarchy":
        barrier = threading.Barrier(2)

        def step(step_input):
            with ct.span("inside-step"):
                barrier.wait(timeout=10)
                return StepOutput(content="done")

        with ct.span("workflow-request") as root:
            output = Workflow(
                name="workflow",
                steps=[
                    Parallel(
                        Step(name="a", executor=step), Step(name="b", executor=step)
                    )
                ],
                telemetry=False,
            ).run("question")
        assert output.content
        result = balanced()
        workflow = next(s for s in result if s.name == "workflow workflow")
        assert workflow.parent.span_id == root.context.span_id
        inner = [s for s in result if s.name == "inside-step"]
        assert len(inner) == 2
        byid = {s.context.span_id: s for s in result}
        assert {byid[s.parent.span_id].name for s in inner} == {"step a", "step b"}
        m, calls = model()
        member, _ = model()
        with ct.span("team-request") as root:
            output = Team(
                name="coordinator",
                model=m,
                members=[Agent(name="member", model=member, telemetry=False)],
                telemetry=False,
            ).run("question")
        assert output.content == "done"
        result = balanced()
        team = next(s for s in result if s.name == "invoke_agent coordinator")
        assert team.parent.span_id == root.context.span_id
        assert (
            len(
                [
                    s
                    for s in children(result, team)
                    if s.attributes.get("gen_ai.operation.name") == "chat"
                ]
            )
            == len(calls)
            == 1
        )
elif mode == "stream":
    m, calls = model()
    agent = Agent(name="assistant", model=m, telemetry=False)
    with ct.span("request"):
        outer = current()
        iterator = agent.run("question", stream=True)
        chunks = []
        for chunk in iterator:
            assert current() == outer
            chunks.append(chunk.content or "")
        assert "".join(chunks) == "done"

    async def stream_async():
        with ct.span("async-request"):
            outer = current()
            chunks = []
            async for chunk in agent.arun("question", stream=True):
                assert current() == outer
                chunks.append(chunk.content or "")
            assert "".join(chunks) == "done"

    asyncio.run(stream_async())
    result = balanced()
    assert len(operation(result, "chat")) == len(calls) + (mode == "ownership") == 2
    assert all(
        "done" in s.attributes["confident.span.output"]
        for s in operation(result, "invoke_agent")
    )
elif mode == "concurrency":

    async def asynchronous():
        async def one(i):
            m, calls = model(tool="lookup")
            with ct.span(f"async-{i}"):
                parent = current()
                result = await Agent(model=m, tools=[lookup], telemetry=False).arun(
                    "question"
                )
                assert current() == parent
            assert result.content == "done"
            assert len(calls) == 2

        await asyncio.gather(*(one(i) for i in range(4)))

    asyncio.run(asynchronous())
    with ThreadPoolExecutor(max_workers=2) as pool:
        with ct.span("thread-request") as root:
            jobs = [
                pool.submit(
                    contextvars.copy_context().run, run, f"worker-{i}", tool="lookup"
                )
                for i in range(4)
            ]
            assert all(len(job.result()) == 2 for job in jobs)
        assert pool.submit(current).result().span_id == 0
    result = balanced()
    byid = {s.context.span_id: s for s in result}
    for request in (s for s in result if s.name.startswith("async-")):
        trace_spans = [
            s for s in result if s.context.trace_id == request.context.trace_id
        ]
        assert len(operation(trace_spans, "invoke_agent")) == 1
        assert len(operation(trace_spans, "chat")) == 2
        assert len(operation(trace_spans, "execute_tool")) == 1
    for s in result:
        if s.parent:
            assert s.context.trace_id == byid[s.parent.span_id].context.trace_id
    assert len({s.context.trace_id for s in result if s.name.startswith("async-")}) == 4
elif mode == "termination":
    fn = Function.from_callable(
        lambda: (_ for _ in ()).throw(ValueError("secret failure")), name="fail"
    )
    result = FunctionCall(function=fn, arguments={}).execute()
    assert result.status == "failure"
    (tool,) = operation(balanced(), "execute_tool")
    assert tool.status.status_code.name == "ERROR"
    m, _ = model()
    iterator = Agent(model=m, telemetry=False).run("question", stream=True)
    next(iterator)
    iterator.close()
    balanced()

    def values():
        for value in ("do", "ne"):
            with ct.span("generator-tool-child"):
                yield value

    invocation = FunctionCall(function=Function.from_callable(values), arguments={})
    output = invocation.execute()
    assert invocation.result is output.result
    outer = current()
    chunks = []
    for value in output.result:
        chunks.append(value)
        assert current() == outer
    assert "".join(chunks) == "done"
    result = balanced()
    toolspan = next(
        s
        for s in operation(result, "execute_tool")
        if s.attributes["gen_ai.tool.name"] == "values"
    )
    assert len(children(result, toolspan)) == 2

    async def cancel():
        entered = asyncio.Event()

        async def wait():
            entered.set()
            await asyncio.Event().wait()

        call = FunctionCall(function=Function.from_callable(wait), arguments={})
        task = asyncio.create_task(call.aexecute())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    result = balanced()
    assert any(
        s.attributes.get("error.type") == "CancelledError"
        and s.status.status_code.name == "ERROR"
        for s in result
    )
elif mode == "lifecycle":
    from confident_trace.integrations import registry

    registry.instrument(rt, ("agno",))
    run()
    assert len(operation(balanced(), "invoke_agent")) == 1
    ct.shutdown()
    assert Agent.run is original
    init("agno")
    # The external pipeline intentionally retains its previous spans.
    run("second")
    ct.flush()
    assert (
        len(
            operation(
                __import__("support").exporter.get_finished_spans(), "invoke_agent"
            )
        )
        == 1
    )
    ct.shutdown()
    assert Agent.run is original

ct.shutdown()
