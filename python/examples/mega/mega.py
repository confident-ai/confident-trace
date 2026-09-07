"""Mixed-framework stress example. See README.md; --offline makes no network calls."""

import argparse
import asyncio
import contextvars
import json
import multiprocessing
import os
import tempfile
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import aclosing
from pathlib import Path

import httpx
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI, OpenAI
from opentelemetry import context, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

import confident_trace as ct

INSTRUMENTATIONS = ("langchain", "openai", "pydantic_ai", "claude_agent_sdk")
_PROCESS_PROVIDER = None
_PROCESS_AUDIT = None


class Audit(SpanProcessor):
    """Observe the existing pipeline, without exporting a second copy of spans."""

    def __init__(self):
        self.lock = threading.Lock()
        self.started = {}
        self.ended = []

    def on_start(self, span, parent_context=None):
        with self.lock:
            self.started[f"{span.context.span_id:016x}"] = (
                f"{span.context.trace_id:032x}"
            )

    def on_end(self, span):
        row = {
            "name": span.name,
            "id": f"{span.context.span_id:016x}",
            "trace": f"{span.context.trace_id:032x}",
            "parent": f"{span.parent.span_id:016x}" if span.parent else None,
            "scope": span.instrumentation_scope.name,
            "operation": span.attributes.get("gen_ai.operation.name"),
            "status": span.status.status_code.name,
            "conversation": span.attributes.get("gen_ai.conversation.id"),
            "pid": os.getpid(),
            "start_ns": span.start_time,
            "end_ns": span.end_time,
        }
        with self.lock:
            self.ended.append(row)

    def snapshot(self, trace_id=None):
        with self.lock:
            return {
                "started": [
                    s
                    for s, t in self.started.items()
                    if trace_id is None or t == trace_id
                ],
                "spans": [
                    s for s in self.ended if trace_id is None or s["trace"] == trace_id
                ],
            }


def initialize(offline, provider):
    # A real exporter in live mode also lets the Claude adapter configure its CLI.
    kwargs = {"exporter": InMemorySpanExporter()} if offline else {}
    return ct.init(
        tracer_provider=provider,
        instrumentations=INSTRUMENTATIONS,
        **kwargs,
    )


def offline_response(request):
    """Real SDK parsing and tracing, with a deterministic HTTP/SSE response."""
    body = json.loads(request.content)
    result = {
        "id": "chat-offline",
        "object": "chat.completion",
        "created": 1,
        "model": body["model"],
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "offline hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }
    if body.get("stream"):
        frames = []
        for delta, finish in [
            ({"role": "assistant", "content": "offline "}, None),
            ({"content": "hello"}, None),
            ({}, "stop"),
        ]:
            frames.append(
                {
                    **result,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    "usage": result["usage"] if finish else None,
                }
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(f) + "\n\n" for f in frames)
            + "data: [DONE]\n\n",
        )
    return httpx.Response(200, json=result)


def http_client(offline, asynchronous=False):
    cls = httpx.AsyncClient if asynchronous else httpx.Client
    kwargs = {"transport": httpx.MockTransport(offline_response)} if offline else {}
    return cls(timeout=60, **kwargs)


def sdk(offline, asynchronous=False):
    cls = AsyncOpenAI if asynchronous else OpenAI
    return cls(
        api_key="offline" if offline else os.environ["OPENAI_API_KEY"],
        max_retries=0,
        http_client=http_client(offline, asynchronous),
    )


def current():
    return trace.get_current_span().get_span_context()


def direct_sync(offline, model, label="thread.openai"):
    caller = current()
    with ct.span(label), sdk(offline) as client:
        result = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say hello in three words."}],
        )
        text = result.choices[0].message.content
    assert current() == caller, "Sync caller context was not restored"
    return text


def langchain_job(offline, model):
    @tool
    def sdk_lookup(value: str) -> str:
        """Ask the OpenAI SDK to repeat a short value."""
        with ct.span("tool.sdk"), sdk(offline) as client:
            return (
                client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "user", "content": f"Repeat this briefly: {value}"}
                    ],
                )
                .choices[0]
                .message.content
                or ""
            )

    caller = current()
    # The chain always invokes this real LangChain tool, independently of whether
    # a model would choose to call it. Its inner request uses the ordinary SDK.
    with ct.span("thread.langchain"), http_client(offline) as client:
        chain = (
            ChatPromptTemplate.from_template("Give a three-word label for {topic}.")
            | ChatOpenAI(
                model=model,
                api_key="offline" if offline else os.environ["OPENAI_API_KEY"],
                http_client=client,
                max_retries=0,
            )
            | StrOutputParser()
            | RunnableLambda(lambda value: sdk_lookup.invoke({"value": value}))
        )
        result = chain.invoke({"topic": "tracing"})
    assert current() == caller, "Thread caller context was not restored"
    return result


async def pydantic_job(offline, model, label="async.pydantic"):
    caller = current()
    async with sdk(offline, asynchronous=True) as client:
        agent = Agent(
            OpenAIChatModel(model, provider=OpenAIProvider(openai_client=client)),
            retries=0,
        )
        with ct.span(label):
            result = await agent.run("Say hello in three words.")
    assert current() == caller, "Pydantic caller context was not restored"
    return result.output


async def streaming_job(offline, model):
    caller = current()
    async with sdk(offline, asynchronous=True) as client:
        with ct.span("async.openai.stream"):
            parent = current()
            stream = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say hello in three words."}],
                stream=True,
                stream_options={"include_usage": True},
            )
            chunks = []
            try:
                async for chunk in stream:
                    assert current() == parent, (
                        "A stream leaked its active model context"
                    )
                    if chunk.choices:
                        chunks.append(chunk.choices[0].delta.content or "")
            finally:
                await stream.close()
    assert current() == caller, "Async caller context was not restored"
    return "".join(chunks)


async def claude_job(model):
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    # No repository files or tools are needed for this text-only demonstration.
    with (
        ct.span("claude.invocation"),
        tempfile.TemporaryDirectory(prefix="ct-mega-claude-") as directory,
    ):
        options = ClaudeAgentOptions(
            model=model, tools=[], max_turns=1, cwd=directory, setting_sources=[]
        )
        result = None
        async with aclosing(
            query(prompt="Say hello in three words. Do not use tools.", options=options)
        ) as messages:
            async for message in messages:
                if isinstance(message, ResultMessage):
                    # A ResultMessage precedes CLI telemetry shutdown. Drain to
                    # EOF so the enclosing interaction span can end and flush.
                    result = message
        if result is None:
            raise RuntimeError("Claude returned no ResultMessage")
        if result.is_error:
            raise RuntimeError(f"Claude result: {result.subtype}")
        return result.result


def process_job(carrier, conversation, offline, model):
    """Spawned workers initialize their own runtime; only W3C strings cross IPC."""
    global _PROCESS_PROVIDER, _PROCESS_AUDIT
    if _PROCESS_PROVIDER is None:
        _PROCESS_PROVIDER = TracerProvider(
            resource=Resource.create({"service.name": "confident-trace-mega"})
        )
        trace.set_tracer_provider(_PROCESS_PROVIDER)
        _PROCESS_AUDIT = Audit()
        _PROCESS_PROVIDER.add_span_processor(_PROCESS_AUDIT)
    initialize(offline, _PROCESS_PROVIDER)
    token = context.attach(TraceContextTextMapPropagator().extract(carrier))
    trace_id = f"{current().trace_id:032x}"
    results = {}
    try:
        with ct.span("process.worker", thread_id=conversation):
            results["sdk"] = direct_sync(offline, model, "process.openai")
            results["pydantic"] = asyncio.run(
                pydantic_job(offline, model, "process.pydantic")
            )
    except Exception as error:
        results["error"] = type(error).__name__
    finally:
        context.detach(token)
        ct.shutdown()  # Finish export before returning, including on worker errors.
    return {
        "pid": os.getpid(),
        "results": results,
        "audit": _PROCESS_AUDIT.snapshot(trace_id),
    }


async def intentional_failure():
    with ct.span("intentional.failure"):
        raise ValueError("Requested MEGA failure")


async def request_job(index, options, threads, processes, conversation):
    loop = asyncio.get_running_loop()
    response = {"index": index, "results": {}, "errors": {}}
    caller = current()
    try:
        with ct.span(f"mega.request.{index}", thread_id=conversation) as root:
            response["trace"] = f"{root.context.trace_id:032x}"
            carrier = {}
            TraceContextTextMapPropagator().inject(carrier)
            jobs = {
                "langchain": loop.run_in_executor(
                    threads,
                    contextvars.copy_context().run,
                    langchain_job,
                    options.offline,
                    options.model,
                ),
                "sdk-thread": loop.run_in_executor(
                    threads,
                    contextvars.copy_context().run,
                    direct_sync,
                    options.offline,
                    options.model,
                ),
                "pydantic": pydantic_job(options.offline, options.model),
                "sdk-stream": streaming_job(options.offline, options.model),
                "process": loop.run_in_executor(
                    processes,
                    process_job,
                    carrier,
                    conversation,
                    options.offline,
                    options.model,
                ),
            }
            if not options.offline and not options.skip_claude:
                jobs["claude"] = asyncio.wait_for(
                    claude_job(options.claude_model), timeout=120
                )
            else:
                response["results"]["claude"] = "SKIPPED (offline or --skip-claude)"
            if options.fail_one and index == 0:
                jobs["intentional"] = intentional_failure()
            values = await asyncio.gather(*jobs.values(), return_exceptions=True)
            for name, value in zip(jobs, values):
                if isinstance(value, BaseException):
                    response["errors"][name] = type(value).__name__
                else:
                    response["results"][name] = value
                    if name == "process" and "error" in value["results"]:
                        response["errors"][name] = value["results"]["error"]
            if response["errors"]:
                raise RuntimeError("One or more MEGA branches failed")
    except RuntimeError:
        if not response["errors"]:
            raise
    assert current() == caller, "Request leaked context into its caller"
    return response


def verify(report):
    spans, started = report["audit"]["spans"], report["audit"]["started"]
    byid = {s["id"]: s for s in spans}
    assert len(byid) == len(spans), "Duplicate observed span IDs"
    assert sorted(started) == sorted(byid), "Started/ended spans do not balance"
    expected = {r["trace"] for r in report["requests"]}
    assert len(expected) == len(report["requests"]), (
        "Independent requests reused a trace"
    )
    assert {s["trace"] for s in spans} == expected, (
        "A branch started an unrelated trace"
    )
    for s in spans:
        assert s["status"] != "ERROR", f"Error span: {s['name']}"
        if s["parent"]:
            assert s["parent"] in byid, f"Missing parent: {s['name']}"
            assert s["trace"] == byid[s["parent"]]["trace"], "Cross-request parenting"
    for request in report["requests"]:
        tree = [s for s in spans if s["trace"] == request["trace"]]
        (root,) = [s for s in tree if s["name"] == f"mega.request.{request['index']}"]
        assert root["parent"] is None
        assert root["conversation"] == report["conversation"]
        if report["claude_enabled"]:
            (claude,) = [s for s in tree if s["name"] == "claude.invocation"]
            assert claude["parent"] == root["id"]
        for name in (
            "thread.langchain",
            "thread.openai",
            "async.pydantic",
            "async.openai.stream",
            "process.worker",
        ):
            (child,) = [s for s in tree if s["name"] == name]
            assert child["parent"] == root["id"], f"Wrong request parent: {name}"
            assert child["conversation"] == report["conversation"]
        (toolsdk,) = [s for s in tree if s["name"] == "tool.sdk"]
        assert byid[toolsdk["parent"]]["operation"] == "execute_tool"
        assert len([s for s in tree if s["operation"] == "chat"]) == 7, (
            "Expected seven logical model calls, without duplicate inference spans"
        )
        assert (
            len(
                [
                    s
                    for s in tree
                    if s["scope"] == "pydantic-ai" and s["operation"] == "invoke_agent"
                ]
            )
            == 2
        )
        assert len({s["pid"] for s in tree}) == 2, "Missing spawned-process telemetry"


def print_trees(spans):
    children = {}
    for s in sorted(spans, key=lambda s: s["start_ns"]):
        children.setdefault(s["parent"], []).append(s)

    def visit(parent=None, depth=0):
        for s in children.get(parent, []):
            print(
                f"{'  ' * depth}{s['name']} [{s['scope']}; pid={s['pid']}; id={s['id']}]"
            )
            visit(s["id"], depth + 1)

    visit()


async def run(options):
    provider = TracerProvider(
        resource=Resource.create({"service.name": "confident-trace-mega"})
    )
    trace.set_tracer_provider(provider)
    audit = Audit()
    provider.add_span_processor(audit)
    initialize(options.offline, provider)
    conversation = "mega-" + uuid.uuid4().hex[:12]
    try:
        with (
            ThreadPoolExecutor(max_workers=options.threads) as threads,
            ProcessPoolExecutor(
                max_workers=options.processes,
                mp_context=multiprocessing.get_context("spawn"),
            ) as processes,
        ):
            results = await asyncio.gather(
                *(
                    request_job(i, options, threads, processes, conversation)
                    for i in range(options.requests)
                )
            )
    finally:
        ct.shutdown()
        provider.shutdown()
    report = {
        "mode": "offline" if options.offline else "live",
        "claude_enabled": not options.offline and not options.skip_claude,
        "conversation": conversation,
        "requests": results,
        "audit": audit.snapshot(),
        "claude_native_spans": "Not visible to Python audit; verify CLI-native spans and delivery in the backend.",
    }
    for result in results:
        worker = result["results"].get("process")
        if worker:
            worker_audit = worker.pop("audit")
            report["audit"]["started"].extend(worker_audit["started"])
            report["audit"]["spans"].extend(worker_audit["spans"])
    failures = [r for r in results if r["errors"]]
    try:
        assert not failures, f"Branch failures: {[r['errors'] for r in failures]}"
        verify(report)
        report["verification"] = (
            "PASS: Python span hierarchy, inference counts and lifecycle"
        )
    except (AssertionError, ValueError) as error:
        report["verification"] = f"FAIL: {error}"
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, indent=2) + "\n")
    print_trees(report["audit"]["spans"])
    for result in results:
        print(
            f"Request {result['index']}: trace={result['trace']} errors={result['errors']}"
        )
    print(report["verification"])
    print(report["claude_native_spans"])
    print(f"Report: {options.output.resolve()}")
    if options.offline:
        print("OFFLINE: mocked model responses; no backend export; Claude skipped.")
    else:
        print("Live export attempted. Local PASS does not confirm backend receipt.")
    return 1 if report["verification"].startswith("FAIL") else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--skip-claude", action="store_true")
    parser.add_argument(
        "--fail-one",
        action="store_true",
        help="Add one deliberate error; expect exit code 1",
    )
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--claude-model", default=os.getenv("CLAUDE_MODEL"))
    parser.add_argument("--requests", type=int, default=2)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--processes", type=int, default=2)
    parser.add_argument("--output", type=Path, default=Path("mega-trace.json"))
    options = parser.parse_args()
    if min(options.requests, options.threads, options.processes) < 1:
        parser.error("requests, threads and processes must be positive")
    if os.getenv("OTEL_SDK_DISABLED", "").lower() == "true":
        parser.error("Unset OTEL_SDK_DISABLED to run the tracing demonstration")
    if not options.offline:
        if not os.getenv("OPENAI_API_KEY"):
            parser.error("Set OPENAI_API_KEY or use --offline")
        if not options.skip_claude:
            import claude_agent_sdk  # noqa: F401 — fail before starting paid model calls
    raise SystemExit(asyncio.run(run(options)))


if __name__ == "__main__":
    main()
