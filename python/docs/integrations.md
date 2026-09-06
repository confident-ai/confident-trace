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
does not activate Pydantic AI, Strands, Google ADK, or other frameworks' tracing
settings, and does not collect spans from an unrelated provider automatically.

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

Install `pip install -e './python[google-adk]'` (tested with Google ADK **2.8.0**), then call
`init()` before running your runner. Native ADK tracing already emits through the
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

Install `pip install -e './python[agentcore]'` (tested with AgentCore **1.22.0**, OTel ASGI
instrumentation **0.63b1**). `init()` includes `agentcore` by default. The adapter
uses upstream OTel ASGI middleware for HTTP `/invocations` requests without an
active server span. This fixes a verified local-runtime gap: AgentCore's request
context carries session identifiers but does not itself extract W3C trace context.
The middleware uses the application's configured propagator and preserves context
through the response body, including streams. Its server span carries the supplied
session header as `gen_ai.conversation.id`. It does not inspect request/response bodies.

If AWS/application server instrumentation already provides the active server span,
or OTel ASGI middleware is already registered on the application, the adapter delegates without creating another server span or modifying that span.
Framework/application spans are exported unchanged. Other routes, WebSocket, A2A,
and cloud-service internal telemetry are outside this integration's support claim.
AgentCore is a runtime: model, tool, and agent spans still come from provider or
framework instrumentation inside it. Arbitrary functions are not auto-discovered.

Use [the direct Bedrock example](../examples/agentcore/bedrock.py) or
[the native Strands example](../examples/agentcore/strands.py). The latter selects
only `agentcore` because Strands already owns inference instrumentation; automatic
Strands/provider deduplication is not claimed. The example passes AgentCore's
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
implied. The extras do not pin framework versions. ADK duplicate prevention matches
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
