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
represents a turn. The thread ID does not change parentage or merge traces. The optional `turn()`
scope starts a fresh trace for each turn. Explicit thread IDs also populate the standard
`gen_ai.conversation.id` on the entry and subsequent package spans. See `examples/conversation.py` for
adding conversation metadata to an optional custom entry point.

Development install: `pip install -e './python[test]'` from the repository root.
Version 0.1.0 is the initial release; the API may change before 1.0.0. See the release compatibility matrix for coverage.

## What is supported?

- **Automatically instrumented SDK calls:** OpenAI, Anthropic, Google GenAI, and AWS Bedrock Runtime (Boto3).
  We wrap their supported Python methods and emit OTel spans ourselves.
- **In-house framework integration:** LangChain, LangGraph, and Deep Agents; full callback hierarchy,
  GenAI model/tool spans, and standard OTel nesting inside nodes and tools.
  CrewAI, LlamaIndex, Agno and smolagents capture execution structure with
  provider-owned inference spans; see [framework ownership](docs/frameworks.md).
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

`init()` reads `CONFIDENT_API_KEY`, `CONFIDENT_OTEL_ENDPOINT`, `OTEL_SDK_DISABLED`,
`OTEL_RESOURCE_ATTRIBUTES`, and standard OTel exporter settings. Unspecified
exporter options are delegated to OTel, including TLS certificates/client keys,
compression, headers, and timeouts.

Explicit arguments override environment settings. `CONFIDENT_OTEL_ENDPOINT` is
the full traces endpoint. Standard OTel endpoint variables are ignored.
Without an endpoint setting, HTTP export defaults to `https://otel.confident-ai.com/v1/traces`. `OTEL_SDK_DISABLED=true`
always disables package tracing at initialization. Trace-specific exporter
settings take precedence over generic settings. An explicit `endpoint` is the
complete traces endpoint, including `/v1/traces` for HTTP export. Timeouts passed to `init` are seconds; `flush` and `shutdown`
budgets are milliseconds. Explicit `headers` override environment/auth headers.

```sh
export CONFIDENT_API_KEY=...
export OTEL_RESOURCE_ATTRIBUTES='service.name=my-agent,deployment.environment.name=staging'
export CONFIDENT_OTEL_ENDPOINT=http://localhost:4318/v1/traces
# Or a gRPC Collector:
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export CONFIDENT_OTEL_ENDPOINT=http://localhost:4317
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

`@span`, `@span(name="step")`, and `@span(type="tool")` preserve function return
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
Existing wrapt wrappers are not stacked. Enabled native integrations add
`confident.span.integration` to spans from their exact instrumentation scope.
Other attributes, events, and schema URLs retain their native conventions.

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

### LangChain, LangGraph, and Deep Agents

Install the frameworks and model integrations your application uses, then call
`init()`. No tracing extra or OpenInference dependency is required. These frameworks
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

Deep Agents (`deepagents`, Python 3.11+) uses this same bridge automatically via
`init()`. For selective instrumentation, use `instrumentations=("deepagents",)`.
Delegation through `task`, nested subagents, built-in filesystem tools, and
custom tools retain their callback hierarchy. Spans keep the `LangGraph`
integration label. See the [Deep Agents example](examples/deepagents_agent.py).

See [execution and concurrency details](docs/langchain.md) and the offline
[LangGraph example](examples/langgraph_local.py).

CrewAI is automatically instrumented when installed. See [CrewAI setup and tracing ownership](docs/crewai.md).

LlamaIndex, Agno, and smolagents are automatically instrumented when installed.
See [setup, execution context and model span ownership](docs/frameworks.md).

## Integration labels

`confident_trace.Integration` is the shared string enum of Cloud UI labels.
Package-owned integrations stamp `confident.span.integration` at span creation;
enabled native integrations stamp spans on the shared provider. Provider calls
keep their own SDK label when nested inside framework spans. LangGraph uses
`Integration.LANGCHAIN`, matching its shared callback bridge and Cloud UI.

Existing UI labels are retained exactly, including `LangChain`, `PydanticAI`,
`CrewAI`, `LlamaIndex`, `OpenAI Agents`, `Google ADK`, `Strands`, and `AgentCore`.
New integrations use `Google GenAI`, `Bedrock`, `Microsoft Agent Framework`,
`Agno`, and `Smolagents`; Cloud accepts these strings but has no dedicated icons
for them yet. The enum also includes `OpenRouter`, `OpenTelemetry`, and
`OpenInference` for explicitly attributed external spans.

Claude Agent SDK's label is available as `Integration.CLAUDE_AGENT_SDK`, but its
CLI subprocess exports directly to OTLP and bypasses the Python span processor.
Stamping those remote spans requires support in the CLI or the receiving
Collector; this SDK does not add duplicate local spans.

## Manual span and request APIs

Initialize once before traced calls; decorators never initialize exporters. Python
supports `type="agent"`, `"llm"`, `"retriever"`, `"tool"`, or `"custom"` (default).
`kind="step"` / `kind="tool"` remain deprecated aliases; conflicting values raise.
`span` scopes support `with` and `async with`.

`update_span()` updates the current span, and `update_trace()` updates the entry
span (or the current OTel span). Both accept input/output, metadata, context,
retrieval_context, expected_output, tools_called and expected_tools. Explicit
content overrides automatic capture; supplied metadata replaces the object.
Omitted fields remain unchanged. Content redaction and limits apply.

`update_span(model=..., provider=..., input_token_count=...,
output_token_count=..., cost_per_input_token=..., cost_per_output_token=...)`
records the active model span's fields. The same options work on `span(type="llm")`.
Counts are nonnegative integers; rates are finite nonnegative USD per token, not
per million tokens. Zero is retained. No metric execution or price calculation
is performed. Updates require an active recording span.

`turn(thread_id=..., previous=...)` starts a new trace, optionally linked to a
previous SpanContext, and supports sync/async scopes. Alternatively supply
`thread={"id": "chat", "tags": ["support"], "metadata": {"topic": "billing"}}`
on `turn()` or `update_trace()`. Thread fields emit `confident.trace.thread.*`;
ID also emits legacy `confident.trace.thread_id`. Conflicting IDs raise. Thread
metadata/tags remain separate from trace metadata/tags; supplied values replace
within a trace. Backend merging/storage requires receiver support.
`update_trace(test_case_id=...)` emits `confident.trace.test_case_id`; validate
AI Connection linkage with the receiving deployment.

Use `with suppress_tracing():` (or `async with`) before work to skip supported
instrumentation. Use `with project_context(api_key=tenant_key):` before traced
work to select its exporter. These scopes work in undecorated handlers that call
instrumented clients; concurrent requests remain isolated. An active span cannot
switch projects. Keys stay in private context and auth headers, never attributes
or baggage. A fixed custom exporter needs `project_exporter_factory(key)`;
the returned exporters become runtime-owned. Idle routes are bounded to 64 after
successful cleanup; active/queued routes are preserved. Flush/shutdown cover all
owned routes. Independently exporting subprocesses and unrelated exporters keep
their own configuration.

Spans export in normal batches as they finish. There is no late drop flag or
whole-trace buffering. Outcome-based dropping requires collector tail sampling;
a metadata flag without collector configuration drops nothing.

LLM fields on a non-LLM span are skipped with a warning once per incompatible category per process; general
fields still apply. Updates without a recording span remain no-ops. The legacy
`update_llm_span` helper remains a compatibility alias.

The same update helper works on `agent`, `llm`, `retriever`, `tool`, and `custom`
spans. Input, output, metadata, context, retrieval context, expected output, and
called/expected tools are shared fields on every category. Model, provider,
tokens, and per-token costs require an LLM span. No separate category-specific
update imports are needed.

## Ambient trace context

Use `trace_context(**fields)` in Python (`with` or `async with`) or
`traceContext(fields, callback)` in TypeScript to enrich real work without a
synthetic span. Both accept the full trace-update field shape. Existing values
and outer defaults win; tags, metadata, and thread objects are never merged.
With no active trace, defaults apply to traces started inside the scope.
Exiting restores ambient context without undoing fields already written.
Use `update_trace` / `updateTrace` for explicit later replacements.

Metric collections are plain string names supported by span creation, trace context,
trace updates, span updates, and turn creation. They export as
`confident.span.metric_collection` or `confident.trace.metric_collection`,
independent of content capture. Evaluation IDs remain trace-level fields.

```python
with trace_context(metric_collection="answer-checks", test_case_id="case-1", turn_id="turn-1"):
    with span("retrieve", metric_collection="retrieval-checks"):
        update_span(metric_collection="updated-retrieval-checks")
        update_trace(metric_collection="updated-answer-checks")
```

## LiteLLM

With LiteLLM already installed, run `pip install confident-trace`. Native LiteLLM
instrumentation is enabled by default. Initialize before importing function aliases:

```python
import confident_trace as ct
ct.init(instrumentations=("litellm", "openai"))

import litellm
response = litellm.completion(
    model="openai/gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello"}],
)
```

Covers `completion`, `acompletion`, `Router.completion`, and `Router.acompletion`,
including sync/async streaming. One logical LLM span includes the requested model
(or Router alias), returned model, usage, messages and tool calls. Nested Confident
provider spans are suppressed. Existing LiteLLM callbacks are left untouched;
if another instrumentor already owns these calls, select only one instrumentation.
Function aliases saved before `init()` are not retroactively replaced.

For an OpenAI client calling a LiteLLM proxy, configure the exact client base URL:

```python
ct.init(litellm_proxy_urls=("http://localhost:4000/v1",))
```

These spans retain `confident.span.integration=OpenAI` and add
`confident.gateway.name=litellm`; native spans use integration `LiteLLM`.
`gen_ai.provider.name=litellm` identifies the intermediary, without guessing the
upstream provider from an alias. URLs match the origin and full base path, ignoring
trailing slashes; no credentials or URL values are exported.

This is application-side tracing. Gateway-internal retries, fallback attempts,
caching and distributed trace propagation require separate gateway/HTTP OTel
setup. Embeddings, legacy text completions, and other LiteLLM APIs are not covered.
LiteLLM 1.81.0 is tested on Python 3.10 and 1.100.1 on Python 3.13; the latter
fails to import on Python 3.10 due to an upstream `typing.NotRequired` import.

## OpenRouter

With the OpenRouter SDK already installed, run `pip install confident-trace`. Native
OpenRouter instrumentation is enabled by default, or select it explicitly:

```python
import confident_trace as ct
ct.init(instrumentations=("openrouter", "openai"))

from openrouter import OpenRouter
with OpenRouter(api_key="...") as client:
    result = client.chat.send(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "Hello"}],
    )
```

Covers `chat.send` and `chat.send_async`, including streaming, usage, messages,
tool calls and errors. Initialize before caching bound method aliases. Messages
supplied as lists are captured; instrumentation does not consume input generators.

OpenAI SDK calls to `https://openrouter.ai/api/v1` are automatically identified as
OpenRouter gateway calls. Custom endpoints can be listed in
`ct.init(openrouter_proxy_urls=("https://gateway.example/api/v1",))`.
Matching includes the exact origin and path (ignoring trailing slashes); model
names and lookalike domains do not trigger detection. Explicit LiteLLM mappings
have precedence if the same endpoint is configured for both gateways.

Native spans use integration `OpenRouter`; OpenAI client spans retain `OpenAI`
and add `confident.gateway.name=openrouter`. Both use provider name `openrouter`,
with requested and returned model names kept separate. Upstream routing is not
inferred. Existing content privacy settings apply. This covers application-side
chat calls, not gateway internals, embeddings, Responses, or the Agent SDK.
Tested with `openrouter==1.1.136`.

## Portkey

With the Portkey SDK already installed, run `pip install confident-trace`. Native
Portkey instrumentation is enabled by default, or select it explicitly:

```python
import confident_trace as ct
ct.init(instrumentations=("portkey", "openai"))

from portkey_ai import Portkey
with Portkey(api_key="...", provider="@openai-prod") as client:
    result = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Hello"}],
    )
```

Covers `chat.completions.create` and `responses.create` on `Portkey` and
`AsyncPortkey`, including streaming, usage, tool calls, errors and early stream
closure. Initialize before caching bound methods. Clients created by
`with_options()` remain instrumented; Portkey headers and routing configuration
are preserved. Existing content capture and redaction settings apply.

OpenAI SDK calls to `https://api.portkey.ai/v1` are automatically identified as
Portkey gateway calls. For self-hosted/custom endpoints, use
`ct.init(portkey_proxy_urls=("https://gateway.example/v1",))`.
Exact origin and base path must match; trailing slashes are ignored. Matching
uses the existing LiteLLM → OpenRouter → Portkey precedence for overlapping
configurations. No keys, headers or gateway URLs are exported.

Native spans use integration `Portkey`; OpenAI SDK proxy spans retain `OpenAI`
and add `confident.gateway.name=portkey`. Provider name is `portkey`, without
guessing upstream providers from aliases or config IDs. Requested and returned
models stay separate. Confident-owned nested provider spans are suppressed.

Tested with `portkey-ai==2.3.4` on Python 3.10/3.13. This covers application-side
calls, not Portkey's internal routing/retries, prompt-management endpoints,
embeddings, separate streaming helpers, or gateway-side OTel configuration.

### Bifrost gateway

Bifrost calls through the OpenAI and Anthropic SDKs use their existing automatic
instrumentation. Register each client's full base URL explicitly:

```python
import confident_trace as ct
from openai import OpenAI

ct.init(bifrost_proxy_urls=[
    "http://localhost:8080/openai",
    "http://localhost:8080/anthropic",
])
client = OpenAI(base_url="http://localhost:8080/openai", api_key="<virtual-key>")
client.chat.completions.create(model="openai/gpt-4o-mini", messages=[])
```

Supported surfaces are OpenAI chat completions/Responses and Anthropic Messages,
including sync/async calls and streams. Spans retain their `OpenAI` or `Anthropic`
SDK integration and set `confident.gateway.name` and `gen_ai.provider.name` to
`bifrost`. Matching uses the exact origin and base path, ignoring trailing slashes;
localhost is not detected automatically. URLs and virtual-key headers are not
exported. This captures application calls, not Bifrost's internal routing, retries,
fallbacks or background inference polling lifecycle. Native Go, GenAI and Bedrock
clients are outside this integration's scope.

### TrueFoundry gateway

Register your full TrueFoundry gateway base URL to identify calls made through
OpenAI or Anthropic clients:

```python
import os
import confident_trace as ct
from openai import OpenAI
from anthropic import Anthropic

base_url = os.environ["TRUEFOUNDRY_GATEWAY_BASE_URL"]
api_key = os.environ["TRUEFOUNDRY_API_KEY"]
ct.init(truefoundry_proxy_urls=[base_url])
openai_client = OpenAI(base_url=base_url, api_key=api_key)
anthropic_client = Anthropic(
    base_url=base_url,
    api_key=api_key,
    default_headers={"Authorization": f"Bearer {api_key}"},
)
```

Call the clients normally using your TrueFoundry model names. OpenAI chat
completions/Responses and Anthropic Messages use the existing SDK hooks, including
sync/async calls and streaming helpers. Spans retain `OpenAI` or `Anthropic` as
`confident.span.integration` and set gateway/provider identity to `truefoundry`.
Exact origin and base path matching ignores trailing slashes. No hostname or model
name guessing is used; configure custom or hosted endpoints explicitly. Client
URLs and authentication headers are not exported. This is application-side tracing;
Google GenAI/Bedrock gateway detection and gateway-internal routing/retries are
outside this integration's scope.
