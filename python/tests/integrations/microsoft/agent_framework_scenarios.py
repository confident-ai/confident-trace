import asyncio
import json

import httpx
import pytest
from agent_framework import (
    Agent,
    AgentSession,
    WorkflowBuilder,
    WorkflowContext,
    executor,
)
from agent_framework.exceptions import ChatClientException
from agent_framework.openai import OpenAIChatClient
from conftest import spans
from openai import APIConnectionError, AsyncOpenAI
from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.trace import StatusCode

import confident_trace as ct


def response(output=None):
    return {
        "id": "resp-1",
        "object": "response",
        "created_at": 1,
        "model": "gpt-test",
        "status": "completed",
        "output": output
        if output is not None
        else [
            {
                "type": "message",
                "id": "msg-1",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "hello",
                        "annotations": [],
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
    }


def client_for(handler):
    return AsyncOpenAI(
        api_key="offline",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream, service_session",
    [(False, False), (True, False), (False, True), (True, True)],
    ids=["buffered-local", "stream-local", "buffered-service", "stream-service"],
)
async def test_agents_streams_sessions_and_direct_calls(
    native, stream, service_session
):
    def handler(request):
        body = response()
        if json.loads(request.content).get("stream"):
            events = [
                {
                    "type": "response.created",
                    "response": {**body, "status": "in_progress", "output": []},
                },
                {
                    "type": "response.output_text.delta",
                    "delta": "hello",
                    "item_id": "msg-1",
                    "output_index": 0,
                    "content_index": 0,
                },
                {"type": "response.completed", "response": body},
            ]
            return httpx.Response(
                200,
                text="".join("data: " + json.dumps(e) + "\n\n" for e in events),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=body)

    async with client_for(handler) as client:
        agent = Agent(
            client=OpenAIChatClient(model="gpt-test", async_client=client),
            name="assistant",
        )

        async def run(session_id):
            with ct.span("request"):
                ct.update_trace(thread_id=session_id)
                result = agent.run(
                    "hi",
                    session=AgentSession(
                        session_id=session_id,
                        service_session_id=session_id if service_session else None,
                    ),
                    stream=stream,
                )
                if stream:
                    updates = [update async for update in result]
                    assert "".join(update.text for update in updates) == "hello"
                else:
                    assert (await result).text == "hello"

        await asyncio.gather(run("a"), run("b"))
        captured = spans(native[1])
        model = [
            s for s in captured if s.attributes.get("gen_ai.operation.name") == "chat"
        ]
        agents = [
            s
            for s in captured
            if s.attributes.get("gen_ai.operation.name") == "invoke_agent"
        ]
        assert len(model) == len(agents) == 2
        assert all(s.instrumentation_scope.name == "agent_framework" for s in model)
        assert {s.parent.span_id for s in model} == {s.context.span_id for s in agents}
        assert len({s.context.trace_id for s in agents}) == 2
        requests = {s.context.span_id: s for s in captured if s.name == "request"}
        for agent_span in agents:
            parent = requests[agent_span.parent.span_id]
            assert agent_span.context.trace_id == parent.context.trace_id
            if service_session:
                assert (
                    agent_span.attributes["gen_ai.conversation.id"]
                    == parent.attributes["confident.trace.thread_id"]
                )
            else:
                # 1.17.0 maps service_session_id, not the local session_id.
                assert "gen_ai.conversation.id" not in agent_span.attributes

        assert {
            s.attributes.get("confident.trace.thread_id")
            for s in captured
            if s.name == "request"
        } == {"a", "b"}
        assert tuple(captured) == native[2].get_finished_spans()
        await client.responses.create(model="gpt-test", input="direct")
        assert (
            len(
                [
                    s
                    for s in spans(native[1])
                    if s.instrumentation_scope.name == "confident_trace"
                    and s.name != "request"
                ]
            )
            == 1
        )
        assert not trace.get_current_span().get_span_context().is_valid


@pytest.mark.asyncio
async def test_tool_calls_keep_their_provider_spans(native):
    async with client_for(
        lambda request: httpx.Response(200, json=response())
    ) as direct:

        async def lookup(city: str) -> str:
            """Look up a city."""
            await direct.responses.create(model="gpt-test", input=city)
            return city

        def handler(request):
            body = json.loads(request.content)
            if "function_call_output" in json.dumps(body):
                return httpx.Response(200, json=response())
            return httpx.Response(
                200,
                json=response(
                    [
                        {
                            "type": "function_call",
                            "id": "fc-1",
                            "call_id": "call-1",
                            "name": "lookup",
                            "arguments": '{"city":"Macau"}',
                            "status": "completed",
                        }
                    ]
                ),
            )

        async with client_for(handler) as client:
            agent = Agent(
                client=OpenAIChatClient(model="gpt-test", async_client=client),
                name="assistant",
                tools=[lookup],
            )
            await agent.run("lookup")
    captured = spans(native[1])
    tools = [
        s
        for s in captured
        if s.attributes.get("gen_ai.operation.name") == "execute_tool"
    ]
    own = [s for s in captured if s.instrumentation_scope.name == "confident_trace"]
    assert len(tools) == len(own) == 1
    assert own[0].parent.span_id == tools[0].context.span_id


@pytest.mark.asyncio
async def test_workflow_parentage(native):
    @executor
    async def first(message: str, ctx: WorkflowContext[str]):
        await ctx.send_message(message)

    @executor
    async def last(message: str, ctx: WorkflowContext[None, str]):
        await ctx.yield_output(message)

    workflow = (
        WorkflowBuilder(start_executor=first, name="offline")
        .add_edge(first, last)
        .build()
    )
    # Building a workflow emits a separate validation span. Inspect only run
    # spans so every span below must descend from the request under test.
    ct.flush()
    native[1].clear()
    with ct.span("request"):
        result = await workflow.run("hello")
        assert result.get_outputs() == ["hello"]
    captured = spans(native[1])
    root = next(s for s in captured if s.name == "request")
    native_spans = [s for s in captured if s is not root]
    assert native_spans
    assert [s for s in captured if s.parent is None] == [root]
    assert all(s.context.trace_id == root.context.trace_id for s in native_spans)
    by_id = {s.context.span_id: s for s in captured}
    for child in native_spans:
        assert child.instrumentation_scope.name == "agent_framework"
        visited = set()
        while child is not root:
            assert child.context.span_id not in visited
            visited.add(child.context.span_id)
            child = by_id[child.parent.span_id]
    workflows = [s for s in native_spans if s.name == "workflow.run"]
    assert len(workflows) == 1
    assert workflows[0].parent.span_id == root.context.span_id


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_failure_and_cancellation(native, cancel):
    tracker = SpanBalance()
    native[0].add_span_processor(tracker)
    started = asyncio.Event()

    async def handler(request):
        started.set()
        if cancel:
            await asyncio.Event().wait()
        raise ValueError("offline")

    async with client_for(handler) as client:
        agent = Agent(client=OpenAIChatClient(model="gpt-test", async_client=client))
        task = asyncio.ensure_future(agent.run("hi"))
        await asyncio.wait_for(started.wait(), 5)
        if cancel:
            task.cancel()
        with pytest.raises(
            asyncio.CancelledError if cancel else ChatClientException
        ) as error:
            await task
        if not cancel:
            assert isinstance(error.value.__cause__, APIConnectionError)
            assert isinstance(error.value.__cause__.__cause__, ValueError)
            assert str(error.value.__cause__.__cause__) == "offline"
    captured = spans(native[1])
    assert len(captured) == 2
    assert {s.attributes["gen_ai.operation.name"] for s in captured} == {
        "chat",
        "invoke_agent",
    }
    assert all(s.instrumentation_scope.name == "agent_framework" for s in captured)
    assert tracker.started == tracker.ended == {s.context.span_id for s in captured}
    assert all(s.end_time is not None and s.end_time >= s.start_time for s in captured)
    assert not trace.get_current_span().get_span_context().is_valid
    for ended in captured:
        if cancel:
            # Native 1.17.0 closes both spans, but catches Exception rather than
            # CancelledError (BaseException), leaving status UNSET. Preserve it.
            assert ended.status.status_code == StatusCode.UNSET
        else:
            assert ended.status.status_code == StatusCode.ERROR
            assert ended.attributes["error.type"] == "ChatClientException"


class SpanBalance(SpanProcessor):
    def __init__(self):
        self.started = set()
        self.ended = set()

    def on_start(self, span, parent_context=None):
        self.started.add(span.context.span_id)

    def on_end(self, span):
        self.ended.add(span.context.span_id)


@pytest.fixture
def native_integrations():
    return ("microsoft_agent_framework", "openai")
