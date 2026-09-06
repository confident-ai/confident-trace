# Python integrations


## How support works

There are two distinct paths. OTel-first describes our span/context/export
architecture; it does not mean every provider SDK creates OTel spans itself.

| Source | Who creates the spans? | What `init()` does | Current support and validation |
|---|---|---|---|
| OpenAI Python: Chat Completions / Responses `create` | Confident Trace wraps the SDK methods and creates OTel spans | Automatically instruments the installed SDK | Sync, async, streaming; GenAI 1.37.0 inference attributes; mocked provider tests |
| Anthropic Python: Messages `create` / `stream` | Confident Trace wraps the SDK methods and creates OTel spans | Automatically instruments the installed SDK | Sync, async, streaming; GenAI 1.37.0 inference attributes; mocked provider tests |
| Google GenAI Python: `generate_content` / `generate_content_stream` | Confident Trace wraps the SDK methods and creates OTel spans | Automatically instruments the installed SDK | Sync/async surfaces; GenAI 1.37.0 inference attributes; mocked sync and async streaming tests |
| An SDK/framework already emitting OTel with GenAI conventions | The SDK/framework's own instrumentation | Adds export to the shared SDK TracerProvider; does not enable the framework's instrumentation | Standard span export is supported; richer backend interpretation depends on emitted attributes/version. No framework-specific end-to-end certification in this release |
| An external OTel instrumentor | That instrumentor | Exports its spans on the shared provider, unchanged | Transport interoperability; conventions and API coverage belong to the external instrumentor, not this release's 1.37.0 pin |
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
- https://github.com/DataDog/dd-trace-py/blob/main/ddtrace/contrib/internal/openai/patch.py

Datadog was reviewed for patch ownership and stream lifecycle patterns. No Datadog
source was copied; there is no Datadog dependency or custom writer/agent layer.

