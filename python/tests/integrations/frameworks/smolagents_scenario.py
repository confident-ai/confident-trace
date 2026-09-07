"""Actual tool-calling and local code agents with an offline OpenAI transport."""

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from smolagents import CodeAgent, OpenAIModel, ToolCallingAgent, tool
from smolagents.agents import MultiStepAgent
from smolagents.memory import FinalAnswerStep
from smolagents.utils import AgentGenerationError
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

original = MultiStepAgent._run_stream
rt = init("smolagents")
tool_barrier = None


@tool
def lookup(value: str) -> str:
    """Look up a value.

    Args:
        value: The value to look up.
    """
    with ct.span("inside-tool"):
        if tool_barrier is not None:
            tool_barrier.wait(timeout=10)
        direct_call()
        with provider.get_tracer("application").start_as_current_span("third-party"):
            return "done"


def agent(*, code=False, **kwargs):
    sync, _, calls = client(code=code, final_tool=not code, **kwargs)
    model = OpenAIModel("gpt-4o-mini", api_key="test")
    model.client = sync
    cls = CodeAgent if code else ToolCallingAgent
    return cls(model=model, tools=[lookup], max_steps=3, verbosity_level=-1), calls


def run(name="request", **kwargs):
    a, calls = agent(**kwargs)
    with ct.span(name):
        assert a.run("question") == "done"
    return calls


if mode in ("hierarchy", "ownership", "content", "disabled"):
    calls = run(tool="lookup")
    assert len(calls) == 2
    result = balanced()
    if mode == "disabled":
        assert not result
    else:
        root = next(s for s in result if s.name == "request")
        (invocation,) = operation(result, "invoke_agent")
        assert invocation.parent.span_id == root.context.span_id
        assert len(operation(result, "chat")) == len(calls) + (mode == "ownership")
        (toolspan,) = [
            s
            for s in operation(result, "execute_tool")
            if s.attributes["gen_ai.tool.name"] == "lookup"
        ]
        assert [s.name for s in children(result, toolspan)] == ["inside-tool"]
        model = next(
            s
            for s in operation(result, "chat")
            if s.parent.span_id == toolspan.parent.span_id
        )
        assert model.parent.span_id == toolspan.parent.span_id
        assert {s.context.trace_id for s in result} == {root.context.trace_id}
        if mode == "content":
            assert not any("hello" in str(dict(s.attributes)) for s in result)
    if mode == "hierarchy":
        assert len(run("code-request", code=True)) == 1
        result = balanced()
        assert len([s for s in result if s.name == "inside-tool"]) == 2
        assert len(operation(result, "chat")) == 3
elif mode == "stream":
    a, calls = agent(tool="lookup")
    a.stream_outputs = True
    with ct.span("request"):
        outer = current()
        chunks = []
        for chunk in a.run("question", stream=True):
            chunks.append(chunk)
            assert current() == outer
        assert isinstance(chunks[-1], FinalAnswerStep)
        assert chunks[-1].output == "done"
    result = balanced()
    assert len(operation(result, "chat")) == len(calls) + (mode == "ownership") == 2
    assert (
        "done"
        in operation(result, "invoke_agent")[0].attributes["confident.span.output"]
    )
elif mode == "concurrency":
    with ThreadPoolExecutor(max_workers=2) as pool:
        with ct.span("request") as root:
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
    assert len(operation(result, "invoke_agent")) == 4
    assert len(operation(result, "chat")) == 8
    for s in result:
        if s.parent:
            assert s.context.trace_id == byid[s.parent.span_id].context.trace_id
    assert {s.context.trace_id for s in result} == {root.context.trace_id}
    tool_barrier = threading.Barrier(2)
    before = len(result)
    assert len(run("parallel-tools", tool="lookup", parallel=True)) == 2
    result = balanced()[before:]
    toolspans = [
        s
        for s in operation(result, "execute_tool")
        if s.attributes["gen_ai.tool.name"] == "lookup"
    ]
    assert len(toolspans) == 2
    assert len({s.parent.span_id for s in toolspans}) == 1
    assert all(s.status.status_code.name == "UNSET" for s in toolspans)
    assert len(operation(result, "chat")) == 2
    tool_barrier = None
elif mode == "termination":
    a, calls = agent(fail=True)
    with pytest.raises(AgentGenerationError):
        a.run("question")
    assert len(calls) == 1
    result = balanced()
    (invocation,) = operation(result, "invoke_agent")
    assert invocation.status.status_code.name == "ERROR"
    a, _ = agent(tool="lookup")
    iterator = a.run("question", stream=True)
    next(iterator)
    # Upstream yields an ActionStep from finally during this early-close path.
    with pytest.raises(RuntimeError, match="generator ignored GeneratorExit"):
        iterator.close()
    iterator.close()
    balanced()
elif mode == "lifecycle":
    from confident_trace.integrations import registry

    registry.instrument(rt, ("smolagents",))
    run()
    assert len(operation(balanced(), "invoke_agent")) == 1
    ct.shutdown()
    assert MultiStepAgent._run_stream is original
    init("smolagents")
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
    assert MultiStepAgent._run_stream is original

ct.shutdown()
