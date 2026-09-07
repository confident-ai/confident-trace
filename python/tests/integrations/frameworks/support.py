"""Offline transport and exact span accounting shared by framework scenarios."""

import json
import os
import sys

import httpx
from conftest import spans
from openai import AsyncOpenAI, OpenAI
from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace._core import runtime


class Witness(SpanProcessor):
    def __init__(self):
        self.started = []
        self.ended = []

    def on_start(self, span, parent_context=None):
        self.started.append(span.context.span_id)

    def on_end(self, span):
        self.ended.append(span.context.span_id)


mode = sys.argv[1]
provider = TracerProvider(shutdown_on_exit=False)
witness = Witness()
external = InMemorySpanExporter()
provider.add_span_processor(witness)
provider.add_span_processor(SimpleSpanProcessor(external))
exporter = None
framework_label = None


def init(framework):
    global exporter, framework_label
    framework_label = {
        "agno": "Agno",
        "llamaindex": "LlamaIndex",
        "smolagents": "Smolagents",
    }[framework]
    exporter = InMemorySpanExporter()
    if mode == "disabled":
        os.environ["OTEL_SDK_DISABLED"] = "true"
    ct.init(
        tracer_provider=provider,
        exporter=exporter,
        instrumentations=(framework, "openai"),
        capture_content=mode != "content",
    )
    return runtime.current()


def captured():
    result = spans(exporter)
    if mode != "disabled":
        assert tuple(result) == external.get_finished_spans()
    for span in result:
        if span.instrumentation_scope.name != "confident_trace":
            continue
        operation = span.attributes.get("gen_ai.operation.name")
        if operation in ("invoke_agent", "execute_tool"):
            assert span.attributes["confident.span.integration"] == framework_label
        elif operation == "chat":
            assert span.attributes["confident.span.integration"] == "OpenAI"
    return result


def balanced():
    result = captured()
    assert len(witness.started) == len(set(witness.started))
    assert sorted(witness.started) == sorted(witness.ended), [
        (s.name, s.context.span_id) for s in result
    ]
    return result


def current():
    return trace.get_current_span().get_span_context()


def children(result, parent):
    return [
        s for s in result if s.parent and s.parent.span_id == parent.context.span_id
    ]


def operation(result, name):
    return [s for s in result if s.attributes.get("gen_ai.operation.name") == name]


def client(
    *, tool=None, final_tool=False, code=False, fail=False, wait=None, parallel=False
):
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
        has_tool = any(m["role"] in ("tool", "tool-response") for m in data["messages"])
        # smolagents encodes tool observations as user messages.
        has_tool = has_tool or any(
            "Observation:" in str(m.get("content")) for m in data["messages"]
        )
        if tool and (len(calls) == 1 if final_tool else not has_tool):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-lookup",
                        "type": "function",
                        "function": {"name": tool, "arguments": '{"value": "hello"}'},
                    }
                ],
            }
            reason = "tool_calls"
        elif final_tool:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-final",
                        "type": "function",
                        "function": {
                            "name": "final_answer",
                            "arguments": '{"answer": "done"}',
                        },
                    }
                ],
            }
            reason = "tool_calls"
        else:
            message = {
                "role": "assistant",
                "content": 'Thought: answer\n<code>final_answer(lookup("hello"))</code>'
                if code
                else "done",
            }
            reason = "stop"
        if (
            parallel
            and message.get("tool_calls")
            and message["tool_calls"][0]["function"]["name"] != "final_answer"
        ):
            original = message["tool_calls"][0]
            message["tool_calls"] = [
                {
                    **original,
                    "id": f"call-{i}",
                    "function": {
                        **original["function"],
                        "arguments": json.dumps({"value": str(i)}),
                    },
                }
                for i in range(2)
            ]
        body = {
            "id": "chat-test",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "message": message, "finish_reason": reason}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
        }
        if data.get("stream"):
            # Real SSE including tool requests and usage, not an iterator mock.
            if message.get("tool_calls"):
                deltas = [
                    (
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {**t, "index": i}
                                for i, t in enumerate(message["tool_calls"])
                            ],
                        },
                        None,
                    ),
                    ({}, reason),
                ]
            else:
                deltas = [
                    ({"role": "assistant", "content": "do"}, None),
                    ({"content": "ne"}, None),
                    ({}, "stop"),
                ]
            frames = [
                {
                    **body,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    "usage": body["usage"] if finish else None,
                }
                for delta, finish in deltas
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="".join("data: " + json.dumps(f) + "\n\n" for f in frames)
                + "data: [DONE]\n\n",
            )
        return httpx.Response(200, json=body)

    sync = OpenAI(
        api_key="test",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    asynchronous = AsyncOpenAI(
        api_key="test",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    return sync, asynchronous, calls


def direct_call():
    if mode == "ownership":
        sync, _, calls = client()
        assert (
            sync.chat.completions.create(
                model="gpt-4o-mini", messages=[{"role": "user", "content": "direct"}]
            )
            .choices[0]
            .message.content
            == "done"
        )
        assert len(calls) == 1
