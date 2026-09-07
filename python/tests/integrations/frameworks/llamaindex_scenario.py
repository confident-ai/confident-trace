"""Native dispatch IDs with execution scopes, workflow tasks and provider spans."""

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor

import pytest
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.core.tools import FunctionTool
from llama_index.llms.openai import OpenAI
from llama_index_instrumentation import DispatcherSpanMixin, get_dispatcher
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
from workflows import Workflow, step
from workflows.events import StartEvent, StopEvent

import confident_trace as ct


def lookup(value: str) -> str:
    """Look up a value."""
    with ct.span("inside-tool"):
        direct_call()
        with provider.get_tracer("application").start_as_current_span("third-party"):
            return value


class Retriever(BaseRetriever):
    def _retrieve(self, query_bundle):
        with ct.span("inside-retrieve"):
            return [NodeWithScore(node=TextNode(text=query_bundle.query_str), score=1)]


# Defined before init: mixin-created native decorators must still be bound.
class Execution(DispatcherSpanMixin):
    @get_dispatcher(__name__).span
    def values(self):
        for value in ("do", "ne"):
            Retriever().retrieve(value)
            with ct.span("inside-stream"):
                yield value

    @get_dispatcher(__name__).span
    async def avalues(self):
        for value in ("do", "ne"):
            Retriever().retrieve(value)
            with ct.span("inside-stream"):
                yield value


original = Retriever.retrieve.__wrapped__
rt = init("llamaindex")
assert getattr(rt, "_llamaindex_bridge", None) is not None or mode == "disabled"


def agent():
    sync, asynchronous, calls = client(tool="lookup")
    model = OpenAI(model="gpt-4o-mini", api_key="test", max_retries=0)
    model._client, model._aclient = sync, asynchronous
    return FunctionAgent(
        name="assistant", llm=model, tools=[FunctionTool.from_defaults(lookup)]
    ), calls


async def run(name="request"):
    a, calls = agent()
    with ct.span(name):
        outer = current()
        result = await a.run("question")
        assert current() == outer
        assert str(result) == "done"
    assert len(calls) == 2
    return calls


if mode in ("hierarchy", "ownership", "content", "disabled"):
    asyncio.run(run())
    result = balanced()
    if mode == "disabled":
        assert not result
    else:
        root = next(s for s in result if s.name == "request")
        agents = operation(result, "invoke_agent")
        assert len(agents) == 1
        assert agents[0].parent.span_id == root.context.span_id
        tools = operation(result, "execute_tool")
        assert len(tools) == 1
        assert [s.name for s in children(result, tools[0])] == ["inside-tool"]
        assert len(operation(result, "chat")) == 2 + (mode == "ownership")
        assert {s.context.trace_id for s in result} == {root.context.trace_id}
        byid = {s.context.span_id: s for s in result}
        # Tool execution belongs to workflow structure, never to model inference.
        parent = byid[tools[0].parent.span_id]
        assert parent.attributes.get("gen_ai.operation.name") != "chat"
        if mode == "content":
            assert not any("hello" in str(dict(s.attributes)) for s in result)
    if mode == "hierarchy":
        with ct.span("retrieval-request") as root:
            assert Retriever().retrieve("hello")[0].node.text == "hello"
        result = balanced()
        inner = next(s for s in result if s.name == "inside-retrieve")
        byid = {s.context.span_id: s for s in result}
        assert "retrieve" in byid[inner.parent.span_id].name
        assert inner.context.trace_id == root.context.trace_id
elif mode == "stream":
    with ct.span("request"):
        outer = current()
        chunks = []
        for value in Execution().values():
            chunks.append(value)
            assert current() == outer
        assert "".join(chunks) == "done"

    async def asynchronous():
        with ct.span("async-request"):
            outer = current()
            chunks = []
            async for value in Execution().avalues():
                chunks.append(value)
                assert current() == outer
            assert "".join(chunks) == "done"

    asyncio.run(asynchronous())
    result = balanced()
    byid = {s.context.span_id: s for s in result}
    assert len([s for s in result if s.name == "inside-stream"]) == 4
    assert all(
        byid[s.parent.span_id].name in ("Execution.values", "Execution.avalues")
        for s in result
        if s.name == "inside-stream"
    )

    async def streamed_agent():
        from llama_index.core.agent.workflow import AgentStream

        a, calls = agent()
        with ct.span("streamed-agent"):
            outer = current()
            handler = a.run("question")
            text = []
            async for event in handler.stream_events():
                assert current() == outer
                if isinstance(event, AgentStream):
                    text.append(event.delta)
            assert "".join(text) == "done"
            assert str(await handler) == "done"
        result = balanced()
        assert len(operation(result, "chat")) == len(calls) == 2
        assert not rt._llamaindex_bridge.runs
        assert not rt._llamaindex_bridge.claimed

    asyncio.run(streamed_agent())
elif mode == "concurrency":

    async def concurrent():
        await asyncio.gather(*(run(f"request-{i}") for i in range(4)))

    asyncio.run(concurrent())
    with ThreadPoolExecutor(max_workers=2) as pool:
        with ct.span("thread-request") as root:
            jobs = [
                pool.submit(
                    contextvars.copy_context().run, Retriever().retrieve, "hello"
                )
                for _ in range(4)
            ]
            assert all(job.result()[0].node.text == "hello" for job in jobs)
        assert pool.submit(current).result().span_id == 0
    result = balanced()
    byid = {s.context.span_id: s for s in result}
    assert (
        len({s.context.trace_id for s in result if s.name.startswith("request-")}) == 4
    )
    for request in (s for s in result if s.name.startswith("request-")):
        trace_spans = [
            s for s in result if s.context.trace_id == request.context.trace_id
        ]
        assert len(operation(trace_spans, "invoke_agent")) == 1
        assert len(operation(trace_spans, "chat")) == 2
        assert len(operation(trace_spans, "execute_tool")) == 1
    for s in result:
        if s.parent:
            assert s.context.trace_id == byid[s.parent.span_id].context.trace_id
elif mode == "termination":

    def fail(value: str):
        raise ValueError("secret failure")

    with pytest.raises(ValueError, match="secret failure"):
        FunctionTool.from_defaults(fail).call("hello")
    (tool,) = operation(balanced(), "execute_tool")
    assert tool.status.status_code.name == "ERROR"
    iterator = Execution().values()
    next(iterator)
    iterator.close()
    balanced()

    class Failing(Workflow):
        @step
        async def begin(self, ev: StartEvent) -> StopEvent:
            raise ValueError("secret failure")

    async def fail_workflow():
        with pytest.raises(ValueError, match="secret failure"):
            await Failing().run()

    asyncio.run(fail_workflow())
    result = balanced()
    assert any(
        s.status.status_code.name == "ERROR" and "begin" in s.name for s in result
    )

    async def cancel_workflow():
        from workflows.errors import WorkflowCancelledByUser

        entered = asyncio.Event()

        class Waiting(Workflow):
            @step
            async def begin(self, ev: StartEvent) -> StopEvent:
                entered.set()
                await asyncio.Event().wait()
                return StopEvent(result="unused")

        with ct.span("cancel-request") as request:
            handler = Waiting().run()
            await asyncio.wait_for(entered.wait(), 5)
            await handler.cancel_run()
            with pytest.raises(WorkflowCancelledByUser):
                await handler
        result = balanced()
        invocation = next(s for s in result if s.name == "Waiting.run")
        assert invocation.parent.span_id == request.context.span_id
        # Dispatcher treats user workflow cancellation as normal control flow.
        assert invocation.status.status_code.name == "UNSET"

    asyncio.run(cancel_workflow())
elif mode == "lifecycle":
    from confident_trace.integrations import registry

    registry.instrument(rt, ("llamaindex",))
    asyncio.run(run())
    assert len(operation(balanced(), "invoke_agent")) == 1
    ct.shutdown()
    assert Retriever.retrieve.__wrapped__ is original
    init("llamaindex")
    asyncio.run(run("second"))
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
    assert Retriever.retrieve.__wrapped__ is original

ct.shutdown()
