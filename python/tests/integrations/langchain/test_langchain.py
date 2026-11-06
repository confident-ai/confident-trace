"""Real framework execution, including callback dispatch across task/thread boundaries."""

import asyncio
import contextvars
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import spans
from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace._core import runtime
from confident_trace._semconv import genai_v1_41_0 as ai


class Witness(SpanProcessor):
    def __init__(self):
        self.started = set()
        self.ended = set()

    def on_start(self, span, parent_context=None):
        self.started.add(span.context.span_id)

    def on_end(self, span):
        self.ended.add(span.context.span_id)


@pytest.fixture
def tracing(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(("OTEL_", "LANGCHAIN_", "LANGSMITH_")):
            monkeypatch.delenv(key, raising=False)
    pytest.importorskip("langchain_core")
    pytest.importorskip("langgraph")
    ct.shutdown()
    provider = TracerProvider(shutdown_on_exit=False)
    witness = Witness()
    provider.add_span_processor(witness)
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=provider,
        exporter=exporter,
        instrumentations=("langchain", "langgraph", "openai"),
    )
    bridge = runtime.current()._langchain_bridge
    yield provider, exporter, witness, bridge
    ct.shutdown()
    assert not bridge.runs
    assert witness.started == witness.ended


def model(provider, *, error=None, wait=None):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "test"

        def observe(self):
            with ct.span("manual-model"):
                pass
            with provider.get_tracer("application").start_as_current_span("otel-model"):
                pass

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.observe()
            if error:
                raise error
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="answer",
                            usage_metadata={
                                "input_tokens": 3,
                                "output_tokens": 2,
                                "total_tokens": 5,
                            },
                        )
                    )
                ]
            )

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            self.observe()
            if wait:
                await wait()
            if error:
                raise error
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content="answer"))]
            )

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            for text in ("an", "swer"):
                self.observe()
                if error:
                    raise error
                yield ChatGenerationChunk(message=AIMessageChunk(content=text))

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            for text in ("an", "swer"):
                self.observe()
                if wait:
                    await wait()
                if error:
                    raise error
                yield ChatGenerationChunk(message=AIMessageChunk(content=text))

    return Model()


def named(exporter, name):
    return [s for s in spans(exporter) if s.name == name]


def assert_model_children(exporter):
    captured = spans(exporter)
    models = {
        s.context.span_id
        for s in captured
        if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "chat"
    }
    children = [s for s in captured if s.name in ("manual-model", "otel-model")]
    assert children
    assert all(s.parent.span_id in models for s in children)


def test_graph_hierarchy_and_tools(tracing):
    from typing import TypedDict

    from langchain_core.tools import tool
    from langgraph.graph import END, START, StateGraph

    provider, exporter, _, bridge = tracing
    llm = model(provider)

    @tool
    def lookup(query: str) -> str:
        """Look up a value."""
        with ct.span("manual-tool"):
            pass
        with provider.get_tracer("application").start_as_current_span(
            "otel-tool", attributes={"gen_ai.operation.name": "execute_tool"}
        ):
            pass
        return query.upper()

    class State(TypedDict):
        result: str

    def agent(state):
        llm.invoke("hello")
        return {"result": lookup.invoke({"query": "found"})}

    graph = (
        StateGraph(State)
        .add_node("agent", agent)
        .add_edge(START, "agent")
        .add_edge("agent", END)
        .compile()
    )
    with ct.span("request") as request:
        result = graph.invoke(
            {"result": ""}, {"configurable": {"thread_id": "conversation"}}
        )
        assert trace.get_current_span() is request
    assert result == {"result": "FOUND"}
    root = named(exporter, "LangGraph")[0]
    node = named(exporter, "agent")[0]
    llm_span = named(exporter, "Model")[0]
    tool_span = named(exporter, "lookup")[0]
    assert root.parent.span_id == request.context.span_id
    assert node.parent.span_id == root.context.span_id
    assert llm_span.parent.span_id == tool_span.parent.span_id == node.context.span_id
    assert tool_span.attributes[ai.GEN_AI_OPERATION_NAME] == "execute_tool"
    for name in ("manual-tool", "otel-tool"):
        assert named(exporter, name)[0].parent.span_id == tool_span.context.span_id
    assert_model_children(exporter)
    assert {s.context.trace_id for s in spans(exporter)} == {request.context.trace_id}
    assert root.attributes[ai.GEN_AI_CONVERSATION_ID] == "conversation"
    assert llm_span.attributes[ai.GEN_AI_USAGE_INPUT_TOKENS] == 3
    assert not bridge.runs


@pytest.mark.parametrize("stream", [False, True])
async def test_async_concurrent_models(tracing, stream):
    provider, exporter, _, bridge = tracing
    llm = model(provider)

    async def run(index):
        with ct.span(f"request-{index}") as request:
            if stream:
                chunks = []
                async for chunk in llm.astream("hello"):
                    assert trace.get_current_span() is request
                    chunks.append(chunk.content)
                    await asyncio.sleep(0)
                assert "".join(chunks) == "answer"
            else:
                assert (await llm.ainvoke("hello")).content == "answer"
            assert trace.get_current_span() is request

    await asyncio.gather(*(run(i) for i in range(8)))
    roots = [s for s in spans(exporter) if s.name.startswith("request-")]
    models = named(exporter, "Model")
    assert len(roots) == len(models) == 8
    assert {s.parent.span_id for s in models} == {s.context.span_id for s in roots}
    assert_model_children(exporter)
    assert not bridge.runs


def test_batches_threads_and_parallel_runnables(tracing):
    from langchain_core.runnables import RunnableLambda, RunnableParallel

    provider, exporter, _, bridge = tracing
    llm = model(provider)

    def inner(value):
        with ct.span("inside-worker"):
            return value

    runnable = RunnableParallel(left=RunnableLambda(inner), right=RunnableLambda(inner))
    with ct.span("request") as request:
        assert runnable.batch(list(range(8))) == [
            {"left": i, "right": i} for i in range(8)
        ]
        assert len(llm.batch(["hi"] * 4)) == 4
        with ThreadPoolExecutor(max_workers=2) as pool:
            # Application-owned pools require propagation at submit time.
            futures = [
                pool.submit(contextvars.copy_context().run, llm.invoke, "hi")
                for _ in range(4)
            ]
            assert all(f.result().content == "answer" for f in futures)
        assert trace.get_current_span() is request
    captured = spans(exporter)
    assert {s.context.trace_id for s in captured} == {request.context.trace_id}
    nodes = {s.context.span_id for s in captured if s.name == "inner"}
    assert len(named(exporter, "inside-worker")) == 16
    assert all(s.parent.span_id in nodes for s in named(exporter, "inside-worker"))
    assert_model_children(exporter)
    assert not bridge.runs


@pytest.mark.parametrize("close", [False, True])
def test_stream_context_and_close(tracing, close):
    provider, exporter, _, bridge = tracing
    llm = model(provider)
    with ct.span("request") as request:
        stream = llm.stream("hello")
        first = next(stream)
        assert first.content == "an"
        assert trace.get_current_span() is request
        if close:
            stream.close()
        else:
            assert first.content + "".join(c.content for c in stream) == "answer"
        assert trace.get_current_span() is request
    result = named(exporter, "Model")[0]
    assert result.status.status_code.name != "ERROR"
    if not close:
        assert (
            json.loads(result.attributes[ai.GEN_AI_OUTPUT_MESSAGES])[0]["parts"][0][
                "content"
            ]
            == "answer"
        )
    assert_model_children(exporter)
    assert not bridge.runs


async def test_failure_and_cancellation(tracing):
    provider, exporter, witness, bridge = tracing
    with pytest.raises(ValueError, match="model failed"):
        await model(provider, error=ValueError("model failed")).ainvoke("hello")
    started = asyncio.Event()

    async def wait():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(model(provider, wait=wait).ainvoke("hello"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Async callbacks are shielded tasks; drain their completion before inspecting.
    await asyncio.sleep(0)
    results = named(exporter, "Model")
    assert len(results) == 2
    assert [s.status.status_code.name for s in results] == ["ERROR", "ERROR"]
    assert {s.attributes[ai.ERROR_TYPE] for s in results} == {
        "ValueError",
        "CancelledError",
    }
    assert witness.started == witness.ended
    assert not bridge.runs


async def test_async_stream_cancel_and_close(tracing):
    provider, exporter, witness, bridge = tracing
    stream = model(provider).astream("hi")
    assert (await anext(stream)).content == "an"
    await stream.aclose()
    started = asyncio.Event()

    async def wait():
        started.set()
        await asyncio.Event().wait()

    stream = model(provider, wait=wait).astream("hi")
    task = asyncio.create_task(anext(stream))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    results = named(exporter, "Model")
    assert len(results) == 2
    assert results[0].status.status_code.name != "ERROR"
    assert results[1].status.status_code.name == "ERROR"
    assert witness.started == witness.ended
    assert not bridge.runs


def test_checkpoint_interrupt_resume_and_separate_turns(tracing):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    provider, exporter, _, bridge = tracing

    def ask(state):
        return {"answer": interrupt("question")}

    graph = (
        StateGraph(dict)
        .add_node("ask", ask)
        .add_edge(START, "ask")
        .add_edge("ask", END)
        .compile(checkpointer=InMemorySaver())
    )
    config = {"configurable": {"thread_id": "same-conversation"}}
    assert "__interrupt__" in graph.invoke({}, config)
    assert not bridge.runs
    assert graph.invoke(Command(resume="yes"), config) == {"answer": "yes"}
    roots = named(exporter, "LangGraph")
    assert len(roots) == 2
    assert roots[0].context.trace_id != roots[1].context.trace_id
    assert all(s.status.status_code.name != "ERROR" for s in spans(exporter))
    assert {s.attributes[ai.GEN_AI_CONVERSATION_ID] for s in roots} == {
        "same-conversation"
    }
    asked = named(exporter, "ask")
    assert json.loads(asked[0].attributes["confident.span.output"]) == {
        "__interrupt__": ["question"]
    }
    assert json.loads(asked[1].attributes["confident.span.output"]) == {
        "answer": "yes"
    }
    assert not bridge.runs


def test_user_spans_around_interrupt_are_not_errors(tracing):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    provider, exporter, _, bridge = tracing

    @ct.span(name="decorated-ask")
    def decorated():
        return interrupt("decorated question")

    def ask(state):
        with ct.span("ask-user"):
            first = interrupt("question")
        return {"answer": first, "second": decorated()}

    graph = (
        StateGraph(dict)
        .add_node("ask", ask)
        .add_edge(START, "ask")
        .add_edge("ask", END)
        .compile(checkpointer=InMemorySaver())
    )
    config = {"configurable": {"thread_id": "user-spans"}}
    assert "__interrupt__" in graph.invoke({}, config)
    assert "__interrupt__" in graph.invoke(Command(resume="yes"), config)
    assert graph.invoke(Command(resume="again"), config) == {
        "answer": "yes",
        "second": "again",
    }
    assert len(named(exporter, "ask-user")) == 3
    assert len(named(exporter, "decorated-ask")) == 2
    assert all(s.status.status_code.name != "ERROR" for s in spans(exporter))
    assert not bridge.runs


def test_user_span_errors_inside_graph_nodes_are_still_errors(tracing):
    from langgraph.graph import END, START, StateGraph

    provider, exporter, _, bridge = tracing

    def fail(state):
        with ct.span("work"):
            raise ValueError("boom")

    graph = (
        StateGraph(dict)
        .add_node("fail", fail)
        .add_edge(START, "fail")
        .add_edge("fail", END)
        .compile()
    )
    with pytest.raises(ValueError):
        graph.invoke({})
    (work,) = named(exporter, "work")
    assert work.status.status_code.name == "ERROR"
    assert work.attributes[ai.ERROR_TYPE] == "ValueError"
    assert not bridge.runs


def test_interrupt_control_flow_is_unregistered_on_shutdown(tracing):
    from langgraph.errors import GraphBubbleUp

    from confident_trace._core import spans as core_spans

    assert GraphBubbleUp in core_spans._CONTROL_FLOW
    ct.shutdown()
    assert GraphBubbleUp not in core_spans._CONTROL_FLOW


def test_provider_deduplication_and_direct_calls_in_tools(tracing):
    import httpx
    from langchain_core.callbacks import BaseCallbackHandler
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from openai import OpenAI

    provider, exporter, _, bridge = tracing
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(respond))
    llm = ChatOpenAI(model="gpt-test", api_key="test", http_client=client)
    sdk = OpenAI(api_key="test", http_client=client)
    observed = []

    class Existing(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            observed.append(response)

    @tool
    def direct(query: str) -> str:
        """Call the provider directly."""
        return (
            sdk.chat.completions.create(
                model="gpt-test", messages=[{"role": "user", "content": query}]
            )
            .choices[0]
            .message.content
        )

    with ct.span("request"):
        assert llm.invoke("hello", {"callbacks": [Existing()]}).content == "ok"
        assert direct.invoke({"query": "hello"}) == "ok"
    inference = [
        s
        for s in spans(exporter)
        if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "chat"
    ]
    assert len(calls) == len(inference) == 2
    assert len(observed) == 1
    tool_span = named(exporter, "direct")[0]
    assert (
        len([s for s in inference if s.parent.span_id == tool_span.context.span_id])
        == 1
    )
    assert not bridge.runs
    client.close()


async def test_async_graph_fanout_subgraph_and_tools(tracing):
    from operator import add
    from typing import Annotated, TypedDict

    from langchain_core.tools import tool
    from langgraph.graph import END, START, StateGraph

    provider, exporter, _, bridge = tracing

    class State(TypedDict):
        result: Annotated[list, add]

    @tool
    async def lookup(query: str) -> str:
        """Return a query."""
        await asyncio.sleep(0)
        with ct.span("tool-body"):
            return query

    async def node(state, config):
        with ct.span("node-body"):
            pass
        await model(provider).ainvoke("hi", config=config)
        values = await asyncio.gather(
            *(lookup.ainvoke({"query": str(i)}, config=config) for i in range(3))
        )
        return {"result": values}

    sub = (
        StateGraph(State)
        .add_node("subnode", node)
        .add_edge(START, "subnode")
        .add_edge("subnode", END)
        .compile()
    )
    builder = StateGraph(State).add_node("left", node).add_node("right", sub)
    for name in ("left", "right"):
        builder.add_edge(START, name).add_edge(name, END)
    graph = builder.compile()

    async def request(index):
        with ct.span(f"request-{index}") as current:
            result = await graph.ainvoke({"result": []})
            assert sorted(result["result"]) == ["0", "0", "1", "1", "2", "2"]
            assert trace.get_current_span() is current

    await asyncio.gather(*(request(i) for i in range(4)))
    captured = spans(exporter)
    by_id = {s.context.span_id: s for s in captured}
    assert len(named(exporter, "lookup")) == 24
    for tool_span in named(exporter, "lookup"):
        assert by_id[tool_span.parent.span_id].name in ("left", "subnode")
    for body in named(exporter, "tool-body"):
        assert by_id[body.parent.span_id].name == "lookup"
    for body in named(exporter, "node-body"):
        assert by_id[body.parent.span_id].name in ("left", "subnode")
    for span in captured:
        if span.parent:
            assert by_id[span.parent.span_id].context.trace_id == span.context.trace_id
    assert_model_children(exporter)
    assert not bridge.runs


@pytest.mark.parametrize("mode", ["updates", "values", "messages"])
async def test_graph_streaming_modes(tracing, mode):
    from langgraph.graph import END, START, StateGraph

    provider, exporter, _, bridge = tracing

    async def node(state, config):
        value = await model(provider).ainvoke("hi", config=config)
        return {"text": value.content}

    graph = (
        StateGraph(dict)
        .add_node("node", node)
        .add_edge(START, "node")
        .add_edge("node", END)
        .compile()
    )
    with ct.span("request") as request:
        items = []
        async for item in graph.astream({}, stream_mode=mode):
            items.append(item)
            assert trace.get_current_span() is request
    assert items
    if mode == "updates":
        assert items[-1] == {"node": {"text": "answer"}}
    elif mode == "values":
        assert items[-1] == {"text": "answer"}
    else:
        assert "".join(item[0].content for item in items) == "answer"
    assert_model_children(exporter)
    assert not bridge.runs


async def test_retriever_executor_fallback(tracing):
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever

    provider, exporter, _, bridge = tracing

    class Retriever(BaseRetriever):
        def _get_relevant_documents(self, query, *, run_manager):
            with ct.span("retriever-body"):
                pass
            return [Document(page_content=query)]

    retriever = Retriever()
    with ct.span("request") as request:
        assert retriever.invoke("sync")[0].page_content == "sync"
        assert (await retriever.ainvoke("async"))[0].page_content == "async"
        assert trace.get_current_span() is request
    parents = {s.context.span_id for s in named(exporter, "Retriever")}
    assert len(parents) == 2
    assert all(s.parent.span_id in parents for s in named(exporter, "retriever-body"))
    assert all(
        s.attributes["confident.span.type"] == "retriever"
        for s in named(exporter, "Retriever")
    )
    assert not bridge.runs


def test_content_policy_and_retry(tracing):
    from langchain_core.runnables import RunnableLambda

    provider, exporter, _, bridge = tracing
    attempts = []

    def unreliable(value):
        attempts.append(value)
        if len(attempts) == 1:
            raise ValueError("secret failure message")
        return value

    runnable = RunnableLambda(unreliable).with_retry(
        stop_after_attempt=2, wait_exponential_jitter=False
    )
    assert runnable.invoke("secret") == "secret"
    result = named(exporter, "unreliable")
    assert len(attempts) == 2
    assert len([s for s in result if s.status.status_code.name == "ERROR"]) == 1
    assert all("secret failure message" not in str(s.attributes) for s in result)
    from confident_trace._core.content import ContentPolicy

    bridge.runtime.policy = ContentPolicy(enabled=False)
    model(provider).invoke("hidden")
    result = named(exporter, "Model")[-1]
    assert ai.GEN_AI_INPUT_MESSAGES not in result.attributes
    assert ai.GEN_AI_OUTPUT_MESSAGES not in result.attributes
    assert result.attributes[ai.GEN_AI_USAGE_INPUT_TOKENS] == 3
    bridge.runtime.policy = ContentPolicy(
        redact=lambda value: (_ for _ in ()).throw(ValueError("secret"))
    )
    model(provider).invoke("hidden")
    assert ai.GEN_AI_INPUT_MESSAGES not in named(exporter, "Model")[-1].attributes
    assert not bridge.runs


async def test_graph_early_close(tracing):
    from langgraph.graph import END, START, StateGraph

    provider, exporter, witness, bridge = tracing

    def first(state):
        return {"step": 1}

    def second(state):
        return {"step": 2}

    graph = (
        StateGraph(dict)
        .add_node("first", first)
        .add_node("second", second)
        .add_edge(START, "first")
        .add_edge("first", "second")
        .add_edge("second", END)
        .compile()
    )
    with ct.span("request") as request:
        stream = graph.stream({})
        assert next(stream) == {"first": {"step": 1}}
        stream.close()
        assert trace.get_current_span() is request
        stream = graph.astream({})
        assert await anext(stream) == {"first": {"step": 1}}
        await stream.aclose()
        assert trace.get_current_span() is request
    await asyncio.sleep(0)
    assert not bridge.runs
    assert witness.started == witness.ended
    assert all(s.status.status_code.name != "ERROR" for s in spans(exporter))


async def test_cancelled_graph_worker_finishes_without_leaks(tracing):
    import threading

    from langchain_core.runnables import RunnableLambda

    provider, exporter, witness, bridge = tracing
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def worker(value):
        try:
            with ct.span("inflight-worker"):
                started.set()
                assert release.wait(5)
                with ct.span("late-child"):
                    pass
            return value
        finally:
            finished.set()

    runnable = RunnableLambda(worker)
    task = asyncio.create_task(runnable.ainvoke("hi"))
    await asyncio.to_thread(started.wait, 5)
    assert started.is_set()
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    await asyncio.to_thread(finished.wait, 5)
    assert finished.is_set()
    await asyncio.sleep(0)
    captured = spans(exporter)
    inflight = named(exporter, "inflight-worker")[0]
    assert named(exporter, "late-child")[0].parent.span_id == inflight.context.span_id
    assert witness.started == witness.ended
    assert not bridge.runs
    assert any(s.attributes.get(ai.ERROR_TYPE) == "CancelledError" for s in captured)


async def test_abatch_and_event_stream(tracing):
    provider, exporter, _, bridge = tracing
    llm = model(provider)
    with ct.span("request") as request:
        assert [v.content for v in await llm.abatch(["hi"] * 6)] == ["answer"] * 6
        events = [event async for event in llm.astream_events("hi", version="v2")]
        assert trace.get_current_span() is request
    chunks = [
        event["data"]["chunk"].content
        for event in events
        if event["event"] == "on_chat_model_stream"
    ]
    assert "".join(chunks) == "answer"
    assert len(named(exporter, "Model")) == 7
    assert_model_children(exporter)
    assert not bridge.runs


async def test_tool_failure_and_cancellation(tracing):
    from langchain_core.tools import tool

    provider, exporter, witness, bridge = tracing
    started = asyncio.Event()

    @tool
    async def fail(query: str) -> str:
        """Fail or wait."""
        if query == "fail":
            raise LookupError("private detail")
        started.set()
        await asyncio.Event().wait()
        return "unused"

    with pytest.raises(LookupError, match="private detail"):
        await fail.ainvoke({"query": "fail"})
    task = asyncio.create_task(fail.ainvoke({"query": "wait"}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    results = named(exporter, "fail")
    assert len(results) == 2
    assert {s.attributes[ai.ERROR_TYPE] for s in results} == {
        "LookupError",
        "CancelledError",
    }
    assert all(s.status.status_code.name == "ERROR" for s in results)
    assert witness.started == witness.ended
    assert not bridge.runs


async def test_text_llm_and_shared_batch_backend(tracing):
    from langchain_core.language_models.llms import LLM

    provider, exporter, _, bridge = tracing

    class TextModel(LLM):
        @property
        def _llm_type(self):
            return "test"

        def _call(self, prompt, stop=None, run_manager=None, **kwargs):
            with ct.span("text-backend"):
                return prompt.upper()

    llm = TextModel()
    with ct.span("request"):
        assert llm.invoke("hello") == "HELLO"
        assert (await llm.ainvoke("async")) == "ASYNC"
        assert llm.generate(["one", "two"]).generations[1][0].text == "TWO"
    captured = spans(exporter)
    parents = {
        s.context.span_id
        for s in captured
        if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "text_completion"
    }
    assert len(parents) == 4
    assert all(s.parent.span_id in parents for s in named(exporter, "text-backend"))
    assert not bridge.runs


def test_tool_request_and_execution_ids(tracing):
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tools import tool

    provider, exporter, _, bridge = tracing
    call = {
        "name": "lookup",
        "args": {"query": "hi"},
        "id": "call-42",
        "type": "tool_call",
    }
    llm = FakeMessagesListChatModel(
        responses=[AIMessage(content="", tool_calls=[call])]
    )

    @tool
    def lookup(query: str) -> str:
        """Look up the query."""
        return query.upper()

    def agent(value):
        response = llm.invoke(value)
        return lookup.invoke(response.tool_calls[0])

    assert RunnableLambda(agent).invoke("hello").content == "HI"
    llm_span = named(exporter, "FakeMessagesListChatModel")[0]
    tool_span = named(exporter, "lookup")[0]
    assert llm_span.parent.span_id == tool_span.parent.span_id
    assert tool_span.attributes[ai.GEN_AI_TOOL_CALL_ID] == "call-42"
    parts = json.loads(llm_span.attributes[ai.GEN_AI_OUTPUT_MESSAGES])[0]["parts"]
    assert {
        "type": "tool_call",
        "name": "lookup",
        "arguments": {"query": "hi"},
        "id": "call-42",
    } in parts
    assert not bridge.runs


def test_extraction_failure_is_fail_open(tracing, monkeypatch, caplog):
    import logging

    from confident_trace.integrations.langchain import extraction

    provider, exporter, _, bridge = tracing

    def fail(*args, **kwargs):
        raise ValueError("secret extraction payload")

    monkeypatch.setattr(extraction, "request", fail)
    with caplog.at_level(logging.DEBUG, logger="confident_trace"):
        assert model(provider).invoke("private prompt").content == "answer"
    result = named(exporter, "Model")[0]
    assert result.status.status_code.name != "ERROR"
    assert "secret extraction payload" not in caplog.text
    assert "private prompt" not in caplog.text
    assert not bridge.runs


def test_graph_integration_label_and_isolation(tracing):
    from langgraph.graph import StateGraph, START, END
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    _, exporter, _, _ = tracing
    chain = ChatPromptTemplate.from_template('Explain {topic}.') | FakeListChatModel(responses=['Answer']) | StrOutputParser()

    def node(state):
        return {'topic': chain.invoke(state)}

    graph = StateGraph(dict).add_node('answer', node).add_edge(START, 'answer').add_edge('answer', END).compile(name='Custom graph name')
    graph.invoke({'topic': 'volcanoes'})
    list(graph.stream({'topic': 'penguins'}))

    async def run():
        await graph.ainvoke({'topic': 'bees'})
        return [chunk async for chunk in graph.astream({'topic': 'owls'})]

    asyncio.run(run())
    chain.invoke({'topic': 'standalone'})
    captured = spans(exporter)
    roots = [s for s in captured if s.parent is None]
    assert len(roots) == 5
    for root in roots:
        expected = 'LangGraph' if root.name == 'Custom graph name' else 'LangChain'
        members = [s for s in captured if s.context.trace_id == root.context.trace_id]
        assert all(s.attributes.get('confident.span.integration') == expected for s in members)
        prompt = next(s for s in members if s.name == 'ChatPromptTemplate')
        assert '[unsupported]' not in prompt.attributes['confident.span.output']


def test_graph_message_state_is_a_flat_message_list(tracing):
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    _, exporter, _, _ = tracing

    class State(MessagesState):
        step: int

    graph = (
        StateGraph(State)
        .add_node("reply", lambda state: {"messages": [AIMessage("hello")], "step": 1})
        .add_edge(START, "reply")
        .add_edge("reply", END)
        .compile()
    )
    graph.invoke({"messages": [HumanMessage("hi")], "step": 0})
    root = named(exporter, "LangGraph")[0]
    assert json.loads(root.attributes["confident.span.output"]) == {
        "messages": [
            {"role": "user", "parts": [{"type": "text", "content": "hi"}]},
            {"role": "assistant", "parts": [{"type": "text", "content": "hello"}]},
        ],
        "step": 1,
    }


def test_graph_commands_and_sends_are_tagged_objects(tracing):
    import operator
    from typing import Annotated, TypedDict

    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.types import Command, Send, interrupt

    _, exporter, _, _ = tracing

    class Routed(MessagesState):
        step: int

    (
        StateGraph(Routed)
        .add_node(
            "route",
            lambda state: Command(
                update={"messages": [AIMessage("routed")], "step": 1}, goto="done"
            ),
            destinations=("done",),
        )
        .add_node("done", lambda state: {"step": 2})
        .add_edge(START, "route")
        .add_edge("done", END)
        .compile()
        .invoke({"messages": [HumanMessage("hi")], "step": 0})
    )
    assert json.loads(named(exporter, "route")[0].attributes["confident.span.output"]) == {
        "type": "command",
        "goto": "done",
        "update": {
            "messages": [
                {"role": "assistant", "parts": [{"type": "text", "content": "routed"}]}
            ],
            "step": 1,
        },
    }

    class Fanout(TypedDict):
        items: list
        out: Annotated[list, operator.add]

    (
        StateGraph(Fanout)
        .add_node("work", lambda state: {"out": [state["item"]]})
        .add_conditional_edges(
            START, lambda state: [Send("work", {"item": i}) for i in state["items"]], ["work"]
        )
        .add_edge("work", END)
        .compile()
        .invoke({"items": ["x", "y"], "out": []})
    )
    outputs = [s.attributes.get("confident.span.output", "") for s in spans(exporter)]
    assert json.dumps(
        [
            {"type": "send", "node": "work", "arg": {"item": "x"}},
            {"type": "send", "node": "work", "arg": {"item": "y"}},
        ],
        separators=(",", ":"),
    ) in outputs

    class Asked(TypedDict):
        answer: str

    graph = (
        StateGraph(Asked)
        .add_node("ask", lambda state: {"answer": interrupt("question")})
        .add_edge(START, "ask")
        .add_edge("ask", END)
        .compile(checkpointer=InMemorySaver())
    )
    config = {"configurable": {"thread_id": "resume"}}
    graph.invoke({"answer": ""}, config)
    graph.invoke(Command(resume="yes"), config)
    resumed = named(exporter, "LangGraph")[-1]
    assert json.loads(resumed.attributes["confident.span.input"]) == {
        "type": "command",
        "resume": "yes",
    }
    assert all("[unsupported]" not in output for output in outputs)


def test_graph_pydantic_and_dataclass_state_is_recorded(tracing):
    from dataclasses import dataclass

    from langgraph.graph import END, START, StateGraph
    from pydantic import BaseModel

    _, exporter, _, _ = tracing

    class Model(BaseModel):
        question: str
        answer: str = ""

    @dataclass
    class Data:
        question: str
        answer: str = ""

    @dataclass(slots=True)
    class Slotted:
        question: str
        answer: str = ""

    for schema in (Model, Data, Slotted):
        exporter.clear()
        (
            StateGraph(schema)
            .add_node("answer", lambda state: {"answer": state.question + "!"})
            .add_edge(START, "answer")
            .add_edge("answer", END)
            .compile()
            .invoke(schema(question="why"))
        )
        root = named(exporter, "LangGraph")[0]
        node = named(exporter, "answer")[0]
        assert json.loads(root.attributes["confident.span.input"]) == {
            "question": "why",
            "answer": "",
        }
        assert json.loads(node.attributes["confident.span.input"]) == {
            "question": "why",
            "answer": "",
        }
        assert json.loads(root.attributes["confident.span.output"]) == {
            "question": "why",
            "answer": "why!",
        }


def test_graph_state_scalars_are_recorded(tracing):
    import datetime
    import enum
    import uuid
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    _, exporter, _, _ = tracing

    class Color(str, enum.Enum):
        RED = "red"

    class Level(enum.IntEnum):
        HIGH = 3

    class State(TypedDict):
        at: datetime.datetime
        day: datetime.date
        id: uuid.UUID
        color: Color
        level: Level
        tags: set

    identifier = uuid.UUID("12345678-1234-5678-1234-567812345678")
    (
        StateGraph(State)
        .add_node("touch", lambda state: {"color": Color.RED})
        .add_edge(START, "touch")
        .add_edge("touch", END)
        .compile()
        .invoke(
            {
                "at": datetime.datetime(2026, 1, 2, 3, 4, 5),
                "day": datetime.date(2026, 1, 2),
                "id": identifier,
                "color": Color.RED,
                "level": Level.HIGH,
                "tags": {"a"},
            }
        )
    )
    expected = {
        "at": "2026-01-02T03:04:05",
        "day": "2026-01-02",
        "id": str(identifier),
        "color": "red",
        "level": 3,
        "tags": ["a"],
    }
    root = named(exporter, "LangGraph")[0]
    assert json.loads(root.attributes["confident.span.input"]) == expected
    assert json.loads(root.attributes["confident.span.output"]) == expected
    node = named(exporter, "touch")[0]
    assert json.loads(node.attributes["confident.span.output"]) == {"color": "red"}
