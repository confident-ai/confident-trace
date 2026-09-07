# Confident Trace

OpenTelemetry-first tracing for AI workloads. No Confident wire format, local
agent, replacement provider clients, or dependency on DeepEval.

```python
from confident_trace import init
from openai import OpenAI

init()  # reads CONFIDENT_API_KEY and instruments installed supported SDKs
client = OpenAI()

response = client.responses.create(model="gpt-4.1-mini", input="Hello")
```

That call automatically emits and exports an OTel span. No decorator or manual
trace submission is required. Calls inherit the current OTel context, so calls
inside an already-instrumented agent/request join its trace. Without a parent,
a call starts its own trace. Existing framework OTel spans are exported too.

`@span` is optional: use it to add a custom step or instrument an application
entry point that does not already emit OTel spans. The current provider
instrumentors capture LLM calls and requested tools; arbitrary Python tool
execution needs a framework's OTel instrumentation or a decorator. `init()`
cannot infer request boundaries or tool execution in otherwise uninstrumented
code.

Spans sharing an OTel trace ID make up a trace. Setting
`confident.trace.thread_id` associates that trace with a conversation, where it
represents a turn. This is metadata, not a separate SDK scope or object, and does
not change span parentage or merge traces. Explicit thread IDs also populate the standard
`gen_ai.conversation.id` on the entry and subsequent package spans. See `examples/conversation.py` for
adding conversation metadata to an optional custom entry point.

Development install: `pip install -e './python[test]'` from the repository root.
Version 0.1.0 is the initial release; the API may change before 1.0.0. See the release compatibility matrix for coverage.

## What is supported?

- **Automatically instrumented SDK calls:** OpenAI, Anthropic, Google GenAI, and AWS Bedrock Runtime (Boto3).
  We wrap their supported Python methods and emit OTel spans ourselves.
- **In-house framework integration:** LangChain and LangGraph; full callback hierarchy,
  GenAI model/tool spans, and standard OTel nesting inside nodes and tools.
- **Native framework integration:** Pydantic AI, Strands, Google ADK, Microsoft Agent
  Framework, AgentCore, OpenAI Agents (requires the tracing bridge extra), and
  Claude Agent SDK (native child-process export). See [setup and native limitations](docs/integrations.md).
- **Existing OTel spans:** we export spans an SDK/framework or external instrumentor
  already emits through the shared provider. Framework instrumentation must already
  be enabled; backend GenAI interpretation depends on its conventions.
- **Custom code:** use the optional `@span` decorator.

See the [support mechanisms and version matrix](docs/compatibility.md) for
exact methods, who emits spans, automatic setup, and tested versus unverified
coverage. Existing OTel transport support is not a claim that all agent frameworks
are automatically instrumented or fully mapped.

## Configuration

`init()` reads `CONFIDENT_API_KEY`, `OTEL_SDK_DISABLED`,
`OTEL_RESOURCE_ATTRIBUTES`, and standard OTel exporter settings. Unspecified
exporter options are delegated to OTel, including TLS certificates/client keys,
compression, headers, timeouts and HTTP endpoint path resolution.

Explicit arguments override environment settings. `OTEL_SDK_DISABLED=true`
always disables package tracing at initialization. Trace-specific exporter
settings take precedence over generic settings. An explicit `endpoint` is the
complete traces endpoint; an HTTP generic environment endpoint gets `/v1/traces`
appended by OTel. Timeouts passed to `init` are seconds; `flush` and `shutdown`
budgets are milliseconds. Explicit `headers` override environment/auth headers.

```sh
export CONFIDENT_API_KEY=...
export OTEL_RESOURCE_ATTRIBUTES='service.name=my-agent,deployment.environment.name=staging'
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:4318/v1/traces
# Or a gRPC Collector:
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
# Disable in CI:
export OTEL_SDK_DISABLED=true
```

A supplied API key is forwarded to the configured endpoint. When using a
Collector that does not require it, omit the variable or pass `api_key=""`.
The Confident default endpoint supports HTTP/protobuf. gRPC requires a custom
endpoint. Authentication and project selection are handled by the backend.

`init(tracer_provider=provider)` adds only a standard batch export pipeline to
an existing SDK provider. It does not change its resources, sampler, propagator,
or other processors. Resource arguments apply only when creating a provider.
Without an explicit provider, an existing global provider is reused. Standard
OTel W3C propagation remains available; the package does not instrument HTTP
frameworks or install a different propagator.

Initialization is idempotent; call `shutdown()` before reconfiguring. Initialization
failure returns an inactive runtime and logs a content-free warning. `flush()`
reports whether the queue drained, not whether the remote backend accepted data.
`shutdown(timeout_millis=5000)` returns false if exporter cleanup is still running;
cleanup continues in a daemon thread. It does not shut down an application-owned
provider or its other processors. Finish/close active streams before shutdown.

## Content and spans

`@span`, `@span(name="step")`, and `@span(kind="tool")` preserve function return
values and exceptions. `with span("step") as s:` yields the real OTel span.
`update_trace()` updates the entry span, falling back to the current OTel span.

Content is enabled by default. `init(capture_content=False)` disables content
capture in this package; third-party instrumentors retain their own policies.
`redact(value)` runs before serialization; if it raises, the value is omitted.
`max_content_bytes` defaults to 16 KiB per content attribute. Serialization walks
only built-in containers, with depth/node limits; arbitrary objects become
`[unsupported]`. Oversized custom values become a JSON truncation marker. Message arrays retain a structurally valid prefix, or are omitted if redaction produces an invalid shape. Streaming
capture retains a bounded prefix and marks `confident.span.content_truncated`.
Exception types are recorded without exception messages or stack traces.

Generator decorators start spans on first iteration, detach context between
iterations, and end on exhaustion, close, error, or garbage collection. They
forward send/throw/asend/athrow. Generator yields are not accumulated; a synchronous
generator's final return value is captured. Explicitly close abandoned generators.
Providers capture bounded streaming output separately.

Use `instrumentations=()` when another instrumentor already covers the provider.
Existing wrapt wrappers are not stacked. Third-party OTel spans are exported
unchanged; no semantic-version migration happens inside this SDK.

See the [Python compatibility matrix](docs/compatibility.md) and
[shared wire contract](../spec/contract.md) for exact
supported surfaces, convention versions, release requirements, and portable receiver fixtures. Backend mapping is developed and validated separately.

## License

Licensed under the [Apache License 2.0](LICENSE). The license is included in both
the wheel and source distribution. Bundled OpenTelemetry material retains its
third-party attribution.

Bedrock examples: [Converse](examples/bedrock/converse.py), [streaming](examples/bedrock/streaming.py), and [asyncio thread offload](examples/bedrock/async_converse.py).
Boto3 uses its normal AWS credential chain. Native async AWS clients are not instrumented.

For implementation layout and adding integrations, see the [architecture guide](docs/architecture.md).


Native integrations: install `pip install confident-trace` in your existing Google
ADK or Microsoft Agent Framework application, then call `init()` on the shared
global OTel provider. Supported installed SDKs are detected automatically; manage
your provider and framework dependencies in your application.

AgentCore additionally needs OTel ASGI instrumentation: install
`pip install 'confident-trace[agentcore]'`. This extra installs the tracing
middleware only; your application supplies `bedrock-agentcore`. Extras add tracing
dependencies, rather than enabling integrations.

See [integration setup and boundaries](docs/integrations.md) and
[examples](examples/README.md). Tested versions are documented; cloud deployment
and backend mapping are separate. Test extras are for development and CI.

## SDK diagnostics

To troubleshoot missing telemetry, enable the SDK's diagnostic logger through
Python logging:

```python
import logging

logging.basicConfig(level=logging.WARNING)
logging.getLogger("confident_trace.diagnostics").setLevel(logging.DEBUG)
```

Failures caught by the shared fail-open helper then produce messages such as
`Telemetry operation integrations.openai.extraction.response failed (TypeError)`.
This is SDK troubleshooting output, not a separate telemetry exporter. Existing
application logging handlers and filters determine where the messages go.

Diagnostics are silent unless DEBUG is enabled for this logger (directly or via
its parent). They include only package code locations and exception type names;
arguments, return values, exception messages, and tracebacks are omitted. External
callables use the generic label `telemetry`. Output is limited to 10 messages per
60-second window per process across all operations; the last message announces
suppression. Enabling diagnostics does not change return values or retry calls.
Logging failures are swallowed and forked children get a fresh limiter.
This covers the shared helper, not every upstream or independently caught failure.


For OpenAI Agents framework tracing, install `confident-trace[openai-agents]` in
addition to your existing `openai-agents` package. This extra adds the OpenInference
tracing bridge. Claude Agent SDK needs no extra; `init()` configures its default
subprocess's native OTLP export. See [setup and boundaries](docs/integrations.md).

### LangChain and LangGraph

Install the frameworks and model integrations your application uses, then call
`init()`. No tracing extra or OpenInference dependency is required. Both frameworks
share one owned callback bridge; `instrumentations=("langgraph",)` also enables it.

```python
from confident_trace import init, span

init()
with span("request"):
    result = graph.invoke(state, {"configurable": {"thread_id": "conversation-42"}})
```

Graph nodes, intermediate runnables, models, tools, and retrievers retain their
callback hierarchy. A model's requested tool calls are captured in its output;
subsequent tool executions follow their framework parent, normally alongside
the model under the agent/node. Conversation IDs associate separate traces;
checkpoint resume starts a new invocation.

See [execution and concurrency details](docs/langchain.md) and the offline
[LangGraph example](examples/langgraph_local.py).

CrewAI is automatically instrumented when installed. See [CrewAI setup and tracing ownership](docs/crewai.md).
