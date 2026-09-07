# Python integrations


## How support works

There are two distinct paths. OTel-first describes our span/context/export
architecture; it does not mean every provider SDK creates OTel spans itself.

| Source | Who creates the spans? | What `init()` does | Current support and validation |
|---|---|---|---|
| OpenAI Python: Chat Completions / Responses `create` | Confident Trace wraps the SDK methods and creates OTel spans | Automatically instruments the installed SDK | Sync, async, streaming; GenAI 1.37.0 inference attributes; mocked provider tests |
| Anthropic Python: Messages `create` / `stream` | Confident Trace wraps the SDK methods and creates OTel spans | Automatically instruments the installed SDK | Sync, async, streaming; GenAI 1.37.0 inference attributes; mocked provider tests |
| Google GenAI Python: `generate_content` / `generate_content_stream` | Confident Trace wraps the SDK methods and creates OTel spans | Automatically instruments the installed SDK | Sync/async surfaces; GenAI 1.37.0 inference attributes; mocked sync and async streaming tests |
| An SDK/framework already emitting OTel with GenAI conventions | The SDK/framework's own instrumentation | Adds export to the shared SDK TracerProvider; does not enable the framework's instrumentation | Standard span export is supported; richer backend interpretation depends on emitted attributes/version. See separately verified native integrations below |
| An external OTel instrumentor | That instrumentor | Exports its spans on the shared provider, unchanged | Transport interoperability; conventions and API coverage belong to the external instrumentor, not this release's 1.37.0 pin |
| AWS Bedrock Runtime via Boto3 | Confident Trace wraps Botocore Converse calls | Automatically instruments installed Botocore | Sync Converse and ConverseStream, including real event-stream parsing tests |
| Custom Python functions | Confident Trace's optional `@span` | No automatic discovery of arbitrary functions | Sync, async, generators, async generators tested |

### SDK wrapping (automatic instrumentation)

For OpenAI, `init()` installs `wrapt.FunctionWrapper` wrappers around supported
methods such as `Completions.create` and `Responses.create`. Each wrapper:

1. Starts an OTel span using the active OTel parent context.
2. Extracts request information from the method arguments.
3. Calls the original SDK method once, preserving its result and exceptions.
4. Extracts response/usage information, or observes chunks as the caller consumes
   the stream, and ends the span.

This observes the application-side SDK call, not OpenAI's internal execution.
It requires no replacement client import, SDK fork, or manual input/output update.
The Anthropic and Google integrations use the same mechanism. It captures model
requests and requested tool calls, not the execution of arbitrary tool functions.

### Existing OTel spans (native or externally instrumented)

The source must already have instrumentation enabled and emit through the shared
SDK TracerProvider (or explicitly use the provider passed to `init`). `init()`
enables Pydantic AI and Microsoft Agent Framework as described below. Strands and
Google ADK already emit native spans. Other frameworks must be enabled by the
application. Spans from an unrelated provider are not collected automatically.

We preserve the source's attributes, events, and schema URL. Plain OTel spans can
be exported without GenAI conventions, but exporting a span does not guarantee
that the backend recognizes it as an LLM/tool/agent span or extracts its content.
OpenInference attributes, for example, are not interchangeable with GenAI
attributes; a source-specific mapping must exist in the backend for rich display.

Use `init(instrumentations=())` when external instrumentation already covers
provider calls, or select only the uncovered providers. Existing wrapt wrappers
are skipped, but this is not universal duplicate detection across frameworks.
Third-party capture/redaction settings remain owned by that instrumentation.

The [generated release matrix](compatibility.md) pins only what this package emits. Native and
external senders can emit different convention versions; the backend must handle
those independently. Do not interpret transport support as tested framework parity.

### Instrumented surfaces

- OpenAI: Chat Completions `create` and Responses `create`, sync/async, including
  `stream=True`. OpenAI-compatible clients using these methods are covered.
- Anthropic: Messages `create` and `stream`, sync/async, including `text_stream`.
- Google GenAI: `generate_content` and `generate_content_stream`, sync/async.
- Custom steps/tools: decorators and explicit OTel spans.

Not claimed in 0.1.0: embeddings, realtime, batch APIs, OpenAI's separate
`responses.stream` helper, image/audio generation, remote background polling,
framework callback adapters, or complete provider-specific builtin-tool normalization.
Explicit tool requests on complete responses and OpenAI/Anthropic streaming tool
arguments are captured; unsupported content is marked instead of inspected.
Generator wrappers preserve iteration behavior but are proxy objects, so generator
introspection/type identity is not guaranteed. Custom executors must use normal
OTel/contextvars propagation; no thread monkey-patching is installed.

### Upstream assessment and provenance

Reviewed upstream OpenTelemetry `opentelemetry-instrumentation-genai-openai`
1.1b0 and `opentelemetry-instrumentation-genai-anthropic` 1.1b1, along with the
OpenAI stream buffer implementation. The reviewed OpenAI implementation appends
stream text and tool arguments to lists without a content-size cap. Its current
GenAI conventions advance independently of this package's contract.

This release therefore uses small package-owned instrumentors to enforce bounded
capture and a fixed convention version. No upstream instrumentor source was copied. The attributed convention snapshot and generated constants are maintained separately under spec/. Revisit
upstream adoption when these guarantees can be configured through public hooks.

References:
- https://github.com/open-telemetry/semantic-conventions/tree/v1.37.0/docs/gen-ai
- https://github.com/open-telemetry/opentelemetry-python-genai

## Bedrock Runtime details

`init()` includes `bedrock` in its default instrumentation selection. The wrapper
filters Botocore `_make_api_call` to the `bedrock-runtime` service and `Converse` /
`ConverseStream` operations. Other AWS calls are passed through. It captures model
IDs, inference settings, system/messages, tool requests/results, reported usage,
response request IDs, finish reasons, and an explicitly configured guardrail ID.
It does not infer a resolved model name from an inference-profile ID.

Streaming preserves the response dictionary and wraps only its `stream` value.
Events are observed as the application reads them. Spans end on exhaustion,
stream close, errors, or wrapper collection. Close a stream after stopping early.
Content is bounded and redacted under the same policy as other providers.

Boto3 is synchronous; native async clients are outside this release. Calls made
through `asyncio.to_thread` retain normal context propagation, but cancelling the
await does not cancel a running Boto3 call. AgentCore request-boundary support is described below.

### Upstream assessment

Reviewed the upstream [Botocore Bedrock extension](https://github.com/open-telemetry/opentelemetry-python-contrib/blob/main/instrumentation/opentelemetry-instrumentation-botocore/src/opentelemetry/instrumentation/botocore/extensions/bedrock.py)
on 2026-09-06. It covers Converse and InvokeModel operations but uses legacy
`gen_ai.system` and event-based content paths. The [GenAI migration tracker](https://github.com/open-telemetry/opentelemetry-python-genai/issues/141)
also identifies overlap with Botocore instrumentation. This package keeps its
small Converse wrapper to use the pinned attribute representation and shared
bounded content policy. No upstream instrumentor source was copied.

Do not enable overlapping Bedrock instrumentors. Existing wrapt wrappers are
skipped; this is not universal duplicate detection. Independently emitted OTel
spans continue through the shared provider unchanged.

Bedrock input-token totals include reported cache-read and cache-write counts,
as specified by [AWS prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html).


## Native Google ADK

For an existing Google ADK application, install `pip install confident-trace`, then
call `init()` before running your runner. Install `google-adk` separately when
starting a new application (tested with Google ADK **2.8.0**). Native ADK tracing already emits through the
global OTel provider. There is no OpenInference dependency or extra exporter setup.
The default `google_adk` integration suppresses Confident provider spans only when
the current span is an ADK native inference operation on the shared provider.
Direct provider calls and provider calls made inside tools remain instrumented.
Keep `google_adk` selected when explicitly selecting `google_genai` in an ADK app.

ADK emits `invocation`, `invoke_agent`, `call_llm`, inference, and tool spans.
Its `call_llm` span is retained: it carries content that differs from the native
inference span. This is native framework structure, not an extra Confident span.
Conversation and invocation identifiers pass through unchanged. The observed
scope is `gcp.vertex.agent`, version `2.8.0`, schema `1.36.0`; this does not imply
that every emitted attribute belongs to that schema version.

Content locations verified in the real runner:

| Data | Native representation |
|---|---|
| Session | `gen_ai.conversation.id`; legacy `gcp.vertex.agent.session_id` |
| Invocation | `gcp.vertex.agent.invocation_id` |
| Model input/output | JSON `gcp.vertex.agent.llm_request` / `gcp.vertex.agent.llm_response` on `call_llm` |
| Tool arguments/result | `gcp.vertex.agent.tool_call_args` / `gcp.vertex.agent.tool_response` |
| Usage/finish | `gen_ai.usage.*`, `gen_ai.response.finish_reasons` on native model spans |
| Additional message content | Native OTel logs; this SDK does not configure a logs pipeline |

`ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false` disables ADK's legacy span content;
ADK's own telemetry configuration can override its environment defaults. Confident's
`capture_content`, `redact`, and size bounds govern Confident-owned spans only.
No native spans are rewritten to the package's pinned GenAI convention. Backend
mapping of ADK content and logs is separate and has not been certified here.

See [the runnable agent/tool/streaming example](../examples/google_adk/agent.py).

## Native AgentCore application telemetry

For an existing AgentCore application, install `pip install 'confident-trace[agentcore]'`.
This extra adds only `opentelemetry-instrumentation-asgi`; install `bedrock-agentcore`
separately when starting a new application. Tested with AgentCore **1.22.0** and OTel
ASGI instrumentation **0.63b1**. `init()` includes `agentcore` by default. The adapter
uses upstream OTel ASGI middleware for HTTP `/invocations` requests without an
active server span. This fixes a verified local-runtime gap: AgentCore's request
context carries session identifiers but does not itself extract W3C trace context.
The middleware uses the application's configured propagator and preserves context
through the response body, including streams. Its server span carries the supplied
session header as the Confident extension `confident.trace.thread_id`; native
GenAI conversation attributes are not added or rewritten. It does not inspect request/response bodies.

If AWS/application server instrumentation already provides the active server span,
or OTel ASGI middleware is already registered on the application, the adapter delegates without creating another server span or modifying that span.
Framework/application spans are exported unchanged. Other routes, WebSocket, A2A,
and cloud-service internal telemetry are outside this integration's support claim.
AgentCore is a runtime: model, tool, and agent spans still come from provider or
framework instrumentation inside it. Arbitrary functions are not auto-discovered.

Use [the direct Bedrock example](../examples/agentcore/bedrock.py) or
[the native Strands example](../examples/agentcore/strands.py). The latter uses the default integrations, including Strands/provider deduplication. The example passes AgentCore's
session ID to Strands explicitly using its public `trace_attributes` argument.

In AWS environments, call `init(endpoint=CONFIDENT_TRACES_URL,
protocol="http/protobuf", api_key=...)` explicitly so AWS OTLP endpoint/protocol
variables do not redirect the new exporter. Existing AWS processors, sampling,
resources, and propagators are retained. Unspecified exporter options still follow
standard OTel environment settings. No AWS platform configuration is changed.

## Native lifecycle and compatibility boundaries

Both frameworks must emit through the provider receiving Confident's exporter.
The normal global-provider path supports importing the framework before or after
`init()`. If using `init(tracer_provider=provider)`, register that same provider as
the global provider before framework initialization; passing an unrelated provider
does not redirect native spans. Existing global providers are not replaced.

Versions above describe what was tested, not installation or runtime restrictions.
Other versions may work but have not been verified; historical compatibility is not
implied. Applications manage their own framework dependencies. ADK duplicate prevention matches
native scope and operation regardless of scope version. AgentCore checks its app
and middleware APIs; missing dependencies/capabilities leave the application and
existing native export working. Middleware without `exclude_spans` may also emit
its normal receive/send spans. Constructor failure falls back before invoking the
application; application failures are never retried.

For reproducible tests use `pip install -c python/tests/constraints/native.txt -e
'./python[test,native-test]'` from the repository root. CI separately installs the
latest compatible dependencies without those constraints.
`shutdown()` removes owned hooks and stops only Confident's exporter; framework and
application exporters continue operating. `OTEL_SDK_DISABLED=true` prevents this
package's initialization; it does not dismantle an already configured external pipeline.

Verification uses real ADK runners and AgentCore HTTP handling, mocked model
transports, and native Strands 1.54.0 agent/tool execution. Tests cover sync/async
handlers, concurrent sessions, streams, cancellation, incoming W3C parents, and
an existing OTel server middleware/exporter. Hosted AWS/ADOT smoke tests and backend
UI/content interpretation have **not** been performed. Do not treat local server
coexistence testing as hosted AWS certification.

References: [ADK tracing](https://adk.dev/observability/traces/),
[AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-get-started.html).
DeepEval's working examples informed scenarios; its conversion and evaluation code
was not copied. All span transport remains standard OTLP.


## Microsoft Agent Framework

For an existing Agent Framework application, install `pip install confident-trace`.
When starting a new application, install `agent-framework-core` and the framework's
chosen model client separately (the example uses `agent-framework-openai`).
`init()` enables native instrumentation when needed without configuring new OTel
providers or exporters. A prior `disable_instrumentation()` remains authoritative.
Agents, model calls, tools and workflows retain their native scope and conventions.
Only overlapping Confident model spans are suppressed; model calls made inside
native tools still receive provider spans.

```python
from confident_trace import init

init()
# Construct and run your Agent Framework agents as usual.
```

Use the global provider, or pass that same provider to `init(tracer_provider=...)`.
A separate explicit provider does not redirect framework spans. Use `@span` and
`update_trace(thread_id=...)` around a request for explicit conversation grouping;
framework session identifiers remain framework-owned. In the tested Agent Framework
1.17.0, native `gen_ai.conversation.id` comes from `AgentSession.service_session_id`;
setting only the local `session_id` does not emit that native attribute. See
`examples/microsoft_agent_framework.py`.

Native content is off by default. Configure it with Agent Framework's
`enable_sensitive_telemetry()` or `ENABLE_SENSITIVE_DATA`. Confident's capture,
redaction and size limits apply only to Confident-owned spans. Native metrics and
logs need their own application pipelines. Shutdown removes Confident's exporter
and deduplication registration; it leaves native instrumentation enabled so other
application exporters continue working. Reinitialization adds a fresh pipeline.

Verified with `agent-framework-core==1.17.0` and `agent-framework-openai==1.14.2`:
real agents and workflows, mocked Responses HTTP, tool calls, concurrent sessions,
stream completion, errors/cancellation, and provider/enablement lifecycle. These
are tested versions, not runtime version gates. Failure tests assert both native
`chat` and `invoke_agent` spans end with `ERROR`. Cancellation in 1.17.0 ends both
spans but leaves status `UNSET` because the native handlers catch `Exception`, not
`asyncio.CancelledError`. Confident preserves that native behavior; cancellation
coverage verifies matching started/ended span IDs rather than claiming error status.
Workflow tests run inside a Confident request span and verify a single connected
trace. Microsoft scenarios run in separate processes with an allowlisted environment
and an empty temporary working directory; native settings never leak into other tests.

Sources read for this adapter:
- [Agent Framework observability sample](https://github.com/microsoft/agent-framework/blob/main/python/samples/02-agents/observability/README.md)
- [Native observability API](https://learn.microsoft.com/en-us/python/api/agent-framework-core/agent_framework.observability)


## Pydantic AI and Strands (native OTel)

Install `confident-trace` alongside the framework and model SDK your application
already uses. No Confident extra is needed for either framework. Call `init()`
before running agents; it includes `pydantic_ai` and `strands` by default.

```python
from confident_trace import init, span
from pydantic_ai import Agent

init()
agent = Agent("openai:gpt-4.1-mini")
with span("request", thread_id="conversation-1"):
    result = agent.run_sync("Hello")
```

For Strands, use the normal `strands.Agent` with the same setup. Runnable examples
are [Pydantic AI](../examples/pydantic_agent.py) and
[Strands](../examples/strands_agent.py). Framework agent, model and tool spans
retain their original IDs, parents, events, attributes and conventions. The
optional request span associates the run with your explicit thread ID; it does
not infer or alter a framework's conversation/session identifier.

Pydantic AI is enabled through `Agent.instrument_all(True)` when its process
default is `False`. Existing `InstrumentationSettings` are preserved, including
custom content settings and tracer providers; per-agent overrides remain
Pydantic's responsibility. The default value and an explicit earlier
`Agent.instrument_all(False)` are indistinguishable. To keep native tracing off,
exclude `pydantic_ai` from `instrumentations`, or disable it per agent. On current
Pydantic AI, `agent.instrument = False` is supported; newer native configuration
also offers the `Instrumentation` capability. Configure that through Pydantic's
API. No model methods, agent methods, or capability implementations are patched.
Native enablement remains in place after `shutdown()` for other exporters.

Strands emits OTel spans without calling `StrandsTelemetry` or adding a second
exporter. Its adapter only registers the verified inference scope. An agent
constructed before `init()` can retain OTel's proxy tracer, which resolves when
the global provider is installed. An explicitly configured unrelated provider is
not redirected.

Provider wrappers bypass only when the current span is a recognized native model
operation **on the provider receiving our exporter**. Direct SDK calls and SDK
calls made inside tools still receive Confident spans. Pydantic settings pointing
to an unrelated provider therefore retain Confident provider spans. An explicit
provider shared by both Pydantic and `init(tracer_provider=...)` is supported.
Recognition currently verifies standard SDK processor identity because OTel has
no public span-to-provider API; unknown SDK layouts conservatively retain the
wrapper. Shutdown removes this recognition registration.

Content capture, redaction, error details and semantic-convention versions belong
to the framework. Confident's `capture_content`, size limits and redactor only
apply to Confident spans. In particular, Pydantic's native default captures
content; use its `InstrumentationSettings(include_content=False)` if needed.
No Logfire service, DeepEval adapter, metrics exporter or trace translation is
installed. Backend display/mapping and hosted export remain separately unverified.

Tests use Pydantic AI 2.40.0 and Strands 1.54.0 with real OpenAI model adapters and
mocked HTTP/SSE. They check sync/async execution, streamed output, tools, direct
provider calls, concurrent request parentage, usage, unchanged application-exporter
spans, initialization order, settings preservation and shutdown/reinitialization.
**Strands 1.54.0 leaves native agent/cycle/model spans open on task cancellation
and early stream close.** These are explicit expected failures in the isolated
native test scenarios; this adapter does not repair upstream span lifetimes.
Pydantic's cancellation/early-close paths are checked for complete span closure.

Native API references: [Pydantic AI instrumentation](https://pydantic.dev/docs/ai/integrations/logfire/)
and [Strands tracing](https://strandsagents.com/docs/user-guide/observability-evaluation/traces/).


## OpenAI Agents SDK

In an application already using `openai-agents`, install
`pip install 'confident-trace[openai-agents]'` and call `init()` before agent runs.
The extra installs the **OpenInference OTel bridge**, not the agent framework.
Unlike Pydantic AI, OpenAI Agents' built-in tracing objects are not OTel spans;
the bridge converts them into OTel spans on the provider receiving our exporter.
See the [runnable example](../examples/openai_agent.py).

The `openai_agents` adapter enables `OpenAIAgentsInstrumentor` with
`exclusive_processor=False`. Existing OpenAI tracing processors, including its
default exporter, remain installed. Existing bridge configuration is preserved.
We reuse its agent, task/turn, model, tool, handoff and guardrail tracing rather
than maintaining another conversion layer. Its OpenInference attributes and
content/error policy pass through unchanged; they are not rewritten as GenAI
1.37.0. Confident's content limits and redactor do not govern these spans. Configure
capture with OpenInference's `TraceConfig`/environment settings before `init()` if
needed. Backend interpretation of OpenInference remains separate work.

Our provider wrappers recognize only the bridge's verified scope and `LLM` kind
on the same provider. Independent calls and calls inside tools remain instrumented.
Without the extra, ordinary supported model-client calls can still be traced,
but framework spans are not automatically produced by Confident. Native
`RunConfig(tracing_disabled=True)` remains effective for framework spans and does
not disable separately selected Confident model-client instrumentation.

The bridge owns process-global instrumentation and its original tracer provider.
It remains enabled after Confident shutdown for application exporters; shutdown
removes our inference recognition and exporter gate. Reinitialization reuses the
bridge without adding processors. If you later select a different explicit
provider, the existing bridge is not redirected: use its original provider to
collect its spans. This also applies to an instrumentor configured by the app
before `init()`. Third-party bridge configuration is application-owned.

Tested: OpenAI Agents 0.22.0, OpenInference bridge 2.2.1, OpenAI 3.8.0. Offline
real-SDK tests exercise sync/async runs, Chat Completions and Responses streaming,
tools, handoffs, guardrails, explicit workflows, concurrent parentage, native
processors, failures, cancellation (including cancelling and draining a stream),
disabling and shutdown/reinitialization. Realtime/voice and every hosted tool are
not part of this verified coverage. Native error statuses/content remain upstream
behavior; cancellation tests assert span closure rather than inventing error data.

References: [OpenAI Agents integrations and observability](https://developers.openai.com/api/docs/guides/agents/integrations-observability)
and [OpenInference bridge](https://github.com/Arize-ai/openinference/tree/main/python/instrumentation/openinference-instrumentation-openai-agents).

## Claude Agent SDK (experimental native tracing)

Native export does not currently guarantee a connected application trace. Live
runs with SDK 0.2.152 / CLI 2.1.259 have completed successfully but emitted either
no native batch or standalone `claude_code.llm_request` roots without an
interaction span. The latter batches were accepted by the collector. A CLI
initialization/context race is suspected, not proven; increasing flush timeouts
cannot repair already-disconnected parentage.

For applications requiring one connected trace, disable Claude telemetry per
child and retain an explicit Python invocation span:

```python
options = ClaudeAgentOptions(env={"CLAUDE_CODE_ENABLE_TELEMETRY": "0"})
with ct.span("claude.invocation"):
    async for message in query(prompt="Reply with OK.", options=options):
        pass
```

This gives up native model/tool spans, metrics and logs for that child. The
adapter respects this switch; it does not create the outer span automatically.
Do not combine a replacement model-span bridge with native export, or merge
independent roots by timestamp/session ID. There is no reliable reconciliation
for the observed missing context. See the [investigation](claude-native-tracing.md).

Use `pip install confident-trace` in an application already using
`claude-agent-sdk`, then call `init()` before `query()` or connecting a
`ClaudeSDKClient`. No additional Confident tracing dependency is needed. See the
[runnable example](../examples/claude_agent.py).

Claude Agent SDK runs Claude Code as a child process. Its CLI exports native OTel
spans **directly to the collector**, rather than through the Python TracerProvider.
The `claude_agent_sdk` adapter configures new default subprocess transports with
our resolved OTLP trace endpoint, protocol, headers, explicit timeout/compression,
and the CLI's telemetry/beta-trace enable switches. Other inherited TLS/exporter
settings remain available to the CLI. It configures traces only; metrics/log
pipelines are not enabled by this integration.

Options are copied for each transport: neither `os.environ` nor the caller's
`ClaudeAgentOptions.env` is mutated. Explicit native disable switches are respected.
If `options.env` contains an OTLP exporter setting, its connection configuration
is treated as application-owned as a group; supply its destination and any needed
headers there. Confident authentication is not injected into that override.
Claude's separate detailed-tracing routing configuration is also left untouched.
A caller-supplied Python exporter cannot be serialized for a child process; with
`init(exporter=...)`, explicitly configure the CLI's OTLP destination through its
environment if native agent spans are wanted. `InMemorySpanExporter` will only
receive Python-process spans.

Current SDK versions propagate active W3C context at subprocess connection, so
`query()` inside `ct.span("request")` can join the same distributed trace.
`TRACEPARENT`/`TRACESTATE` in per-agent options remain authoritative. A long-lived
`ClaudeSDKClient` inherits context when it connects, not a fresh parent for every
subsequent query; keep its connection lifetime within the intended parent scope.
Native session IDs and span attributes belong to Claude. Confident thread metadata
and content/redaction limits are not automatically applied to child-process spans.
Custom transports are left untouched.

Shutdown restores our owned constructor patch for future transports. Already
configured transports/connected children retain their configuration. Python
`flush()`/`shutdown()` do not flush the child. For normal `query()` completion,
drain the iterator through EOF, even after receiving `ResultMessage`. Returning
or breaking immediately can terminate the CLI before its enclosing interaction
span ends and exports. Close cancelled streams/clients for cleanup, but do not
assume early closure guarantees native span delivery. Native telemetry is beta, with version-dependent
span names, content, buffering and shutdown behavior.

Tested Python SDK: 0.2.152. Tests run the real `query()`/`ClaudeSDKClient` and
subprocess transport against an **offline CLI protocol fixture**, verifying results,
configuration, parent propagation, explicit overrides, credential isolation,
fail-open behavior and patch ownership. A separate MEGA regression runs the real
bundled CLI (verified with Claude Code 2.1.259) against fake model HTTP responses
and a local OTLP receiver. It asserts two concurrent requests each export exactly
one interaction and one model span, with the request trace ID and exact parent
chain. The fake model URL skips the production remote-settings startup path, so
this does not establish live reliability. The disabled-telemetry case also checks
that only the two Python scopes remain and the query result is preserved. Tool spans,
delivery on cancellation, hosted collector ingestion and backend mapping remain
outside that test's coverage.

Reference: [Claude Agent SDK OpenTelemetry observability](https://code.claude.com/docs/en/agent-sdk/observability).

### LangChain / LangGraph (in-house)

`init()` attaches one inheritable callback bridge and owned execution-context hooks.
Both framework names select the same bridge. The bridge emits our GenAI 1.37.0
spans through the existing OTel pipeline, preserves full callback hierarchy, and
parents custom/third-party spans under the executing node, tool or model. It does
not require OpenInference or a tracing extra. See [setup, concurrency and supported
surfaces](langchain.md).

### CrewAI (in-house execution tracing)

CrewAI execution hooks capture crews, tasks, agents, tools, and flows. Existing
provider adapters own model spans; no extra CrewAI inference span is emitted.
Install framework packages yourself; `init()` includes CrewAI by default. See
[setup, ownership, concurrency, and boundaries](crewai.md).
