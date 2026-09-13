"""Exercise real Deep Agents orchestration with an offline, tool-calling model."""

import asyncio
import json

import pytest
from conftest import spans
from opentelemetry import trace

import confident_trace as ct
from confident_trace._core import runtime
from confident_trace._semconv import genai_v1_37_0 as ai


@pytest.fixture(params=["langgraph", "deepagents"])
def deep_tracing(telemetry, monkeypatch, request):
    pytest.importorskip("deepagents")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    from conftest import enable

    exporter = enable(telemetry, request.param)
    bridge = runtime.current()._langchain_bridge
    yield exporter, bridge
    ct.shutdown()
    assert not bridge.runs


def make_agent(*, checkpointer=None, interrupt=False, fail=False, waiting=None):
    from deepagents import create_deep_agent
    from langchain.agents.middleware import TodoListMiddleware
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool

    class ScriptedModel(BaseChatModel):
        worker: bool = False

        @property
        def _llm_type(self):
            return "offline-deepagents"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.worker and fail:
                raise ValueError("worker model failed")
            calls = []
            if not any(isinstance(m, ToolMessage) for m in messages):
                if self.worker:
                    calls = [
                        {
                            "name": "lookup",
                            "args": {"query": "evidence"},
                            "id": "lookup-1",
                        }
                    ]
                else:
                    calls = [
                        {
                            "name": "task",
                            "args": {
                                "description": topic,
                                "subagent_type": "researcher",
                            },
                            "id": f"task-{i}",
                        }
                        for i, topic in enumerate(("first topic", "second topic"))
                    ] + [
                        {
                            "name": "write_todos",
                            "args": {
                                "todos": [
                                    {
                                        "content": "Research topics",
                                        "status": "in_progress",
                                    }
                                ]
                            },
                            "id": "todos-1",
                        },
                        {
                            "name": "write_file",
                            "args": {
                                "file_path": "/notes.txt",
                                "content": "Research notes",
                            },
                            "id": "file-1",
                        },
                    ]
            message = AIMessage(
                content="" if calls else "Research complete",
                tool_calls=calls,
                usage_metadata={
                    "input_tokens": 7,
                    "output_tokens": 3,
                    "total_tokens": 10,
                },
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

    @tool
    def lookup(query: str) -> str:
        """Return local research evidence."""
        with ct.span("lookup-body"):
            return query.upper()

    @tool
    async def wait_lookup(query: str) -> str:
        """Wait for local research evidence."""
        with ct.span("lookup-body"):
            waiting.set()
            await asyncio.Event().wait()
        return query

    if waiting is not None:
        wait_lookup.name = "lookup"
    return create_deep_agent(
        model=ScriptedModel(),
        name="deep-research",
        middleware=[TodoListMiddleware()],
        subagents=[
            {
                "name": "researcher",
                "description": "Research a topic",
                "system_prompt": "Use lookup.",
                "model": ScriptedModel(worker=True),
                "tools": [wait_lookup if waiting is not None else lookup],
                "interrupt_on": {},
            }
        ],
        checkpointer=checkpointer,
        interrupt_on={"task": True} if interrupt else None,
    )


INPUT = {"messages": [{"role": "user", "content": "Research two topics"}]}


def assert_trace(exporter, bridge, *, requests=1):
    captured = spans(exporter)
    by_id = {s.context.span_id: s for s in captured}
    assert len(by_id) == len(captured)
    tools = [
        s
        for s in captured
        if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "execute_tool"
    ]
    models = [
        s for s in captured if s.attributes.get(ai.GEN_AI_OPERATION_NAME) == "chat"
    ]
    assert len(models) == 6 * requests
    assert len([s for s in tools if s.name == "task"]) == 2 * requests
    for name in ("write_todos", "write_file"):
        assert len([s for s in tools if s.name == name]) == requests
    lookups = [s for s in tools if s.name == "lookup"]
    assert len(lookups) == 2 * requests
    for child in lookups:
        ancestors = []
        current = child
        while current.parent:
            current = by_id[current.parent.span_id]
            assert current.context.trace_id == child.context.trace_id
            ancestors.append(current.name)
        assert "researcher" in ancestors
        assert "task" in ancestors
        assert "deep-research" in ancestors
    bodies = [s for s in captured if s.name == "lookup-body"]
    assert len(bodies) == 2 * requests
    assert all(by_id[s.parent.span_id].name == "lookup" for s in bodies)
    for model in models:
        assert model.attributes[ai.GEN_AI_USAGE_INPUT_TOKENS] == 7
        assert model.attributes[ai.GEN_AI_USAGE_OUTPUT_TOKENS] == 3
        output = json.loads(model.attributes[ai.GEN_AI_OUTPUT_MESSAGES])
        assert output
    assert all(s.status.status_code.name != "ERROR" for s in captured)
    assert not bridge.runs
    return captured


@pytest.mark.parametrize("mode", ["invoke", "ainvoke", "stream", "astream"])
async def test_deepagents_delegation_and_streaming(deep_tracing, mode):
    exporter, bridge = deep_tracing
    agent = make_agent()
    with ct.span("request") as request:
        if mode == "invoke":
            result = agent.invoke(INPUT)
        elif mode == "ainvoke":
            result = await agent.ainvoke(INPUT)
        elif mode == "stream":
            result = list(agent.stream(INPUT, stream_mode="values"))[-1]
        else:
            result = [
                item async for item in agent.astream(INPUT, stream_mode="values")
            ][-1]
        assert result["messages"][-1].content == "Research complete"
        assert trace.get_current_span() is request
    captured = assert_trace(exporter, bridge)
    assert {s.context.trace_id for s in captured} == {request.context.trace_id}


async def test_deepagents_concurrent_requests(deep_tracing):
    exporter, bridge = deep_tracing
    agent = make_agent()

    async def run(index):
        with ct.span(f"request-{index}") as request:
            result = await agent.ainvoke(INPUT)
            assert result["messages"][-1].content == "Research complete"
            assert trace.get_current_span() is request

    await asyncio.gather(*(run(i) for i in range(3)))
    captured = assert_trace(exporter, bridge, requests=3)
    assert len({s.context.trace_id for s in captured}) == 3


async def test_deepagents_interrupt_resume(deep_tracing):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    exporter, bridge = deep_tracing
    agent = make_agent(checkpointer=InMemorySaver(), interrupt=True)
    config = {"configurable": {"thread_id": "deepagent-conversation"}}
    paused = await agent.ainvoke(INPUT, config)
    assert paused["__interrupt__"]
    assert not bridge.runs
    assert not any(s.name == "task" for s in spans(exporter))
    resumed = await agent.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}, {"type": "approve"}]}),
        config,
    )
    assert resumed["messages"][-1].content == "Research complete"
    captured = assert_trace(exporter, bridge)
    roots = [s for s in captured if s.parent is None]
    assert len(roots) == 2
    assert len({s.context.trace_id for s in roots}) == 2
    assert all(
        s.attributes[ai.GEN_AI_CONVERSATION_ID] == "deepagent-conversation"
        for s in roots
    )


async def test_deepagents_worker_error(deep_tracing):
    exporter, bridge = deep_tracing
    with pytest.raises(ValueError, match="worker model failed"):
        await make_agent(fail=True).ainvoke(INPUT)
    captured = spans(exporter)
    assert any(s.attributes.get(ai.ERROR_TYPE) == "ValueError" for s in captured)
    assert not bridge.runs


async def test_deepagents_cancellation(deep_tracing):
    exporter, bridge = deep_tracing
    waiting = asyncio.Event()
    task = asyncio.create_task(make_agent(waiting=waiting).ainvoke(INPUT))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=10)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.sleep(0)
    assert not bridge.runs
    assert any(
        s.attributes.get(ai.ERROR_TYPE) == "CancelledError" for s in spans(exporter)
    )
