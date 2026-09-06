import asyncio

import httpx
import pytest
from conftest import spans

pytest.importorskip("bedrock_agentcore")
pytest.importorskip("opentelemetry.instrumentation.asgi")
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
from bedrock_agentcore.runtime.models import SESSION_HEADER
from opentelemetry import trace
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.trace import SpanKind

import confident_trace as ct


@pytest.mark.asyncio
@pytest.mark.parametrize("preinstrumented", ["none", "outer", "inner"])
@pytest.mark.parametrize("stream", [False, True])
async def test_request_context_and_stream(native, preinstrumented, stream):
    app = BedrockAgentCoreApp()
    tracer = trace.get_tracer(
        "application", "1", schema_url="https://example.test/schema"
    )

    if stream:

        @app.entrypoint
        async def invoke(payload, context):
            with tracer.start_as_current_span("application-step") as span:
                span.set_attribute("session.id", context.session_id)
                span.add_event("native-event", {"value": "unchanged"})
                assert BedrockAgentCoreContext.get_session_id() == context.session_id
                yield {"session": context.session_id}
                await asyncio.sleep(0)
                yield {"done": True}
    else:

        @app.entrypoint
        def invoke(payload, context):
            with tracer.start_as_current_span("application-step") as span:
                span.set_attribute("session.id", context.session_id)
                span.add_event("native-event", {"value": "unchanged"})
                assert BedrockAgentCoreContext.get_session_id() == context.session_id
                return {"session": context.session_id}

    if preinstrumented == "inner":
        app.add_middleware(
            OpenTelemetryMiddleware,
            tracer_provider=native[0],
            exclude_spans=["receive", "send"],
        )
    target = (
        OpenTelemetryMiddleware(
            app, tracer_provider=native[0], exclude_spans=["receive", "send"]
        )
        if preinstrumented == "outer"
        else app
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=target), base_url="http://test"
    ) as client:

        async def request(number):
            trace_id = f"{number:032x}"
            response = await client.post(
                "/invocations",
                json={"prompt": "hello"},
                headers={
                    SESSION_HEADER: f"session-{number}",
                    "traceparent": f"00-{trace_id}-0000000000000042-01",
                },
            )
            assert response.status_code == 200
            assert f"session-{number}" in response.text

        await asyncio.gather(request(1), request(2))
    captured = spans(native[1])
    children = [s for s in captured if s.name == "application-step"]
    assert len(children) == 2
    assert {s.context.trace_id for s in children} == {1, 2}
    server = [s for s in captured if s.kind == SpanKind.SERVER]
    assert len(server) == 2
    if preinstrumented == "none":
        assert {s.attributes["confident.trace.thread_id"] for s in server} == {
            "session-1",
            "session-2",
        }
        assert all("gen_ai.conversation.id" not in s.attributes for s in server)
    assert all(s.parent.span_id == 0x42 for s in server)
    assert {s.parent.span_id for s in children} == {s.context.span_id for s in server}
    assert {s.attributes["session.id"] for s in children} == {"session-1", "session-2"}
    assert all(s.events[0].attributes["value"] == "unchanged" for s in children)
    assert all(
        s.instrumentation_scope.schema_url == "https://example.test/schema"
        for s in children
    )
    assert tuple(captured) == native[2].get_finished_spans()
    assert not trace.get_current_span().get_span_context().is_valid


@pytest.mark.asyncio
async def test_bedrock_inside_agentcore(native):
    import boto3
    from botocore.stub import Stubber

    client = boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload, context):
        return client.converse(
            modelId="test-model",
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
        )

    with Stubber(client) as stub:
        stub.add_response(
            "converse",
            {
                "output": {
                    "message": {"role": "assistant", "content": [{"text": "hello"}]}
                },
                "stopReason": "end_turn",
                "usage": {"inputTokens": 2, "outputTokens": 1, "totalTokens": 3},
                "metrics": {"latencyMs": 1},
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            result = await http.post("/invocations", json={})
            assert result.status_code == 200
    captured = spans(native[1])
    model = [s for s in captured if s.attributes.get("gen_ai.operation.name") == "chat"]
    assert len(model) == 1
    assert model[0].parent.span_id in {
        s.context.span_id for s in captured if s.kind == SpanKind.SERVER
    }
    client.close()


@pytest.mark.asyncio
async def test_exception_and_lifecycle(native):
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload):
        raise ValueError("offline failure")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post("/invocations", json={})).status_code == 500
    assert any(s.status.status_code.name == "ERROR" for s in spans(native[1]))
    ct.shutdown()
    before = len(native[1].get_finished_spans())
    with native[0].get_tracer("application").start_as_current_span("after-shutdown"):
        pass
    assert len(native[1].get_finished_spans()) == before
    assert native[2].get_finished_spans()[-1].name == "after-shutdown"


@pytest.mark.asyncio
async def test_native_strands_tool_inside_agentcore(native):
    pytest.importorskip("strands")
    from strands import Agent, tool
    from strands.models.model import Model

    class OfflineModel(Model):
        def get_config(self):
            return {"model_id": "offline-model"}

        def update_config(self, **kwargs):
            pass

        async def structured_output(self, *args, **kwargs):
            raise NotImplementedError
            yield

        async def stream(self, messages, *args, **kwargs):
            has_result = any("toolResult" in b for m in messages for b in m["content"])
            yield {"messageStart": {"role": "assistant"}}
            if not has_result:
                yield {
                    "contentBlockStart": {
                        "start": {
                            "toolUse": {"toolUseId": "lookup-1", "name": "lookup"}
                        }
                    }
                }
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
            else:
                yield {"contentBlockDelta": {"delta": {"text": "hello"}}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {
                "messageStop": {"stopReason": "end_turn" if has_result else "tool_use"}
            }
            yield {
                "metadata": {
                    "usage": {"inputTokens": 2, "outputTokens": 1, "totalTokens": 3},
                    "metrics": {"latencyMs": 1},
                }
            }

    @tool
    def lookup() -> str:
        """Look up the answer."""
        return "hello"

    app = BedrockAgentCoreApp()

    @app.entrypoint
    async def invoke(payload, context):
        agent = Agent(
            model=OfflineModel(),
            tools=[lookup],
            callback_handler=None,
            trace_attributes={"session.id": context.session_id},
        )
        return str(await agent.invoke_async("hello"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post(
            "/invocations", json={}, headers={SESSION_HEADER: "strands-session"}
        )
        assert result.status_code == 200, result.text
    captured = spans(native[1])
    operations = [s.attributes.get("gen_ai.operation.name") for s in captured]
    assert "invoke_agent" in operations
    assert "execute_tool" in operations
    assert any(s.attributes.get("session.id") == "strands-session" for s in captured)
    assert len({s.context.trace_id for s in captured}) == 1
    assert tuple(captured) == native[2].get_finished_spans()


@pytest.mark.asyncio
async def test_client_disconnect_closes_stream(native):
    app = BedrockAgentCoreApp()
    import threading

    closed = threading.Event()
    sent = asyncio.Event()

    @app.entrypoint
    async def invoke(payload):
        try:
            yield {"text": "first"}
            await asyncio.Event().wait()
        finally:
            closed.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "method": "POST",
        "path": "/invocations",
        "raw_path": b"/invocations",
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 42),
        "headers": [(b"content-type", b"application/json")],
    }

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            sent.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(app(scope, receive, send))
    await asyncio.wait_for(sent.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(closed.wait, 5)
    assert len([s for s in spans(native[1]) if s.kind == SpanKind.SERVER]) == 1
    assert not trace.get_current_span().get_span_context().is_valid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability", ["constructor", "async_call", "setup_failure", "exclude_spans"]
)
async def test_middleware_capabilities_fail_open(native, monkeypatch, capability):
    from opentelemetry.instrumentation import asgi
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    ct.shutdown()
    real = asgi.OpenTelemetryMiddleware
    constructed = []

    class Middleware:
        def __init__(self, app, tracer_provider=None, server_request_hook=None):
            constructed.append(True)
            if capability == "setup_failure":
                raise RuntimeError("unsupported setup")
            self.delegate = real(
                app,
                tracer_provider=tracer_provider,
                server_request_hook=server_request_hook,
            )

        async def __call__(self, scope, receive, send):
            return await self.delegate(scope, receive, send)

    if capability == "constructor":
        monkeypatch.setattr(Middleware, "__init__", lambda self, app: None)
    if capability == "async_call":
        monkeypatch.setattr(Middleware, "__call__", lambda *args: None)
    monkeypatch.setattr(asgi, "OpenTelemetryMiddleware", Middleware)
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter, instrumentations=("agentcore",))
    app = BedrockAgentCoreApp()
    invocations = []

    @app.entrypoint
    def invoke(payload):
        invocations.append(True)
        with trace.get_tracer("application").start_as_current_span("existing-native"):
            return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for _ in range(2):
            assert (await client.post("/invocations", json={})).json() == {"ok": True}
    captured = spans(exporter)
    assert len(invocations) == 2  # Never retry user code on telemetry failure.
    assert len([s for s in captured if s.name == "existing-native"]) == 2
    servers = [s for s in captured if s.kind == SpanKind.SERVER]
    assert len(servers) == (2 if capability == "exclude_spans" else 0)
    assert len(constructed) == (
        1 if capability in ("setup_failure", "exclude_spans") else 0
    )


@pytest.mark.asyncio
async def test_package_versions_do_not_disable_adapters(native, monkeypatch):
    import importlib
    import importlib.metadata

    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from confident_trace._core import runtime
    from confident_trace.integrations._shared.lifecycle import native_inference_active
    from confident_trace.integrations.agentcore import instrumentation as agentcore
    from confident_trace.integrations.google_adk import instrumentation as adk

    ct.shutdown()
    # Reload to catch any imported-by-name metadata.version gate as well.
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "999.0.0")
    importlib.reload(agentcore)
    importlib.reload(adk)
    exporter = InMemorySpanExporter()
    ct.init(exporter=exporter)
    with (
        native[0]
        .get_tracer("gcp.vertex.agent", "999.0.0")
        .start_as_current_span(
            "native-model", attributes={"gen_ai.operation.name": "generate_content"}
        )
    ):
        assert native_inference_active(runtime.current())
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload):
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.post("/invocations", json={})).status_code == 200
    assert len([s for s in spans(exporter) if s.kind == SpanKind.SERVER]) == 1
    ct.shutdown()
    with (
        native[0]
        .get_tracer("gcp.vertex.agent", "999.0.0")
        .start_as_current_span(
            "after-cleanup", attributes={"gen_ai.operation.name": "generate_content"}
        )
    ):
        assert not native_inference_active(runtime.current())
