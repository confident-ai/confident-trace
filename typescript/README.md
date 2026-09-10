# Confident Trace for Node.js

OpenTelemetry tracing for Node.js 22 and 24. Use the same automatic setup for
OpenAI, Anthropic, Google GenAI, Vercel AI SDK, LangChain, LangGraph, Mastra, and
OpenAI Agents. ESM and CommonJS entry points share one runtime.

## Automatic setup

Install Confident Trace alongside the provider/framework packages your app uses:

```sh
npm install confident-trace
```

Set `CONFIDENT_API_KEY` (or configure your OTLP collector), then call `init()` in
your **existing entry file**, before running application work:

```ts
// index.ts — keep your own entry filename
import { init } from 'confident-trace';
import OpenAI from 'openai';

const tracing = init();
const client = new OpenAI();
try {
  const response = await client.responses.create({
    model: 'gpt-4.1-mini',
    input: 'Hello',
  });
  console.log(response.output_text);
} finally {
  await tracing.shutdown();
}
```

Add the preload to the command you already use. Keep the same entry filename:

```sh
# Compiled JavaScript: before → after
node dist/index.js
node --import confident-trace/register dist/index.js

# TypeScript with tsx installed
node --import tsx --import confident-trace/register src/index.ts
```

For example, these package scripts make the flag part of normal startup:

```json
{
  "scripts": {
    "start": "node --import confident-trace/register dist/index.js",
    "dev": "node --import tsx --import confident-trace/register src/index.ts"
  }
}
```

**The hook prepares supported libraries before your entry file loads. `init()`
configures and starts tracing.** Ordinary static imports work; no bootstrap file
or dynamic-import rewrite is needed. Calling `init()` alone does not install the
hook and emits a setup warning when automatic tracing is requested.

Do not add Confident wrappers, callbacks, processors, or exporters to the automatic
examples. Normal application SDK calls remain unchanged:

| Integration   | Complete entry example                             |
| ------------- | -------------------------------------------------- |
| OpenAI        | [openai.ts](examples/auto/openai.ts)               |
| Anthropic     | [anthropic.ts](examples/auto/anthropic.ts)         |
| Google GenAI  | [google-genai.ts](examples/auto/google-genai.ts)   |
| Vercel AI SDK | [vercel-ai.ts](examples/auto/vercel-ai.ts)         |
| LangChain     | [langchain.ts](examples/auto/langchain.ts)         |
| LangGraph     | [langgraph.ts](examples/auto/langgraph.ts)         |
| Mastra        | [mastra.ts](examples/auto/mastra.ts)               |
| OpenAI Agents | [openai-agents.ts](examples/auto/openai-agents.ts) |

Run any source example using the same pattern, for example:
`node --import tsx --import confident-trace/register examples/auto/langchain.ts`.
Use your normal provider credentials; Anthropic and Google examples also read
`ANTHROPIC_MODEL` and `GOOGLE_MODEL`.

Automatic tracing defaults to all supported integrations that are loaded. Select
integrations explicitly or opt out for manual-only/custom tracing:

```ts
init({ instrumentations: ['openai', 'langchain'] });
// Alternatively, for manual-only tracing:
init({ instrumentations: [] });
```

Names are `openai`, `anthropic`, `google-genai`, `vercel-ai`, `langchain`,
`langgraph`, `mastra`, and `openai-agents`. Call `init()` once; subsequent calls
return the existing runtime. `tracing.getInstrumentationStatus()` reports whether
the hook registered and each integration's attachment state. `not observed` means
the corresponding SDK has not been observed, not that it is uninstalled.
Unsupported SDK versions and attachment failures produce a warning.

For servers, initialize once at startup and call `shutdown()` during the server's
existing graceful shutdown, after requests and streams complete. Do not shut down
after each request. Automatic mode coordinates its owned framework adapters;
application-owned exporters/processors retain their ownership. No signal handlers
are installed by Confident Trace. Workers inherit Node's preload arguments by
default but must call `init()` within their own application entry code.

The hook covers SDKs loaded by Node. It does not instrument SDK code bundled into
an application by Webpack, Vite, or another bundler. Use the manual adapters below
with `instrumentations: []` for bundled applications. Existing SDK version and
method coverage is unchanged; automatic setup does not enable extra model APIs.

The package is prepared for distribution but has not been published by this change.
Normal imports do not register tracing, patch SDKs, or send data. The `/register`
preload prepares instrumentation but does not create an exporter or send data until
application initialization.

## Custom tracing

`span(options, fn)` defines a reusable traced function. `withSpan(options, callback)`
runs a block immediately. Both support synchronous and asynchronous work without
turning synchronous return values into promises. Call `init()` once at startup.

```ts
import { span, withSpan, updateSpan, updateTrace } from 'confident-trace';

const retrieve = span(
  { name: 'retrieve', type: 'retriever', metadata: { index: 'support' } },
  async (query: string) => {
    const documents = await search(query);
    updateSpan({ input: query, retrievalContext: documents });
    return documents;
  },
);

const result = await withSpan({ name: 'answer', type: 'agent' }, async () => {
  updateTrace({ input: query, userId, threadId, tags: ['support'] });
  const documents = await retrieve(query);
  const answer = await generateAnswer(query, documents);
  updateSpan({ output: answer, retrievalContext: documents });
  updateTrace({ output: answer, retrievalContext: documents });
  return answer;
});
```

`SpanType` is `agent | llm | retriever | tool | custom`, defaulting to `custom`.
`type: 'tool'` also sets the GenAI tool operation and name. Prefer explicit names:
wrapper defaults use `fn.name` (or `span`), which minification can change.
There are no `updateCurrent*` aliases or decorator compiler requirements.

| Field/API                        | Destination                            |
| -------------------------------- | -------------------------------------- |
| `span` options and `updateSpan`  | Current component's `confident.span.*` |
| `updateTrace` and `turn` options | Entry span's `confident.trace.*`       |
| Span `name`                      | Native OTel span name                  |
| Trace `name`                     | `confident.trace.name`                 |

Both scopes accept `input`, `output`, `metadata`, `retrievalContext`, `context`,
`expectedOutput`, `toolsCalled`, and `expectedTools`. Trace fields additionally
include `tags`, `userId`, `threadId`, `turnId`, and `environment`. Content uses JSON
strings; camelCase API fields map to snake_case attribute suffixes. Tool calls are
plain objects. Backend mapping of evaluation fields requires separate verification;
these helpers do not execute metrics or accept DeepEval metric/test-case objects.

Runtime updates override option defaults; explicit fields win over automatic
capture. `undefined` is omitted; `null`, empty strings/arrays/objects, false, and
zero are preserved. Each supplied field replaces its previous value, including
the whole metadata object. Span updates do not implicitly update trace fields.
Updates after the target span ends are ignored. With no package entry, trace
updates target the current OTel span; with no active span, helpers are no-ops.

Wrappers capture inputs as argument arrays and outputs as returned/fulfilled values.
They do not inspect parameter names or `this`. Scope callbacks have no automatic
input capture; they capture their return values. The entry supplies automatic trace
input/output only when those fields are unset. Promise settlement is preserved;
Promise identity, subclasses, function properties, and constructor behavior are
not guaranteed. Wrappers preserve `this` for normal function calls.

`captureContent: false` on a wrapper/scope disables automatic capture; explicit
helper fields still use the content policy. Global `init({captureContent: false})`
disables all helper content and cannot be overridden by a scope. Bounds/redactors
can be configured per scope and are inherited by nested custom scopes. Failed
redaction omits the value and does not fall back to automatic output.

Options and fields are typed, and unknown keys/unsupported roles are rejected at
runtime. Invalid configuration fails at setup; telemetry failures do not replace
application results or errors. Exception status is recorded without messages or
stack traces. Raw `attributes` must be valid OTel values and bypass helper content
policy. Explicit `type` overrides a raw type attribute; otherwise a valid raw type
is used. Automatic capture preserves explicitly set raw input/output attributes.

`withSpan` and `turn` pass the real OTel span to their callback for advanced usage;
do not end it manually. Application-owned providers work through the registered
OTel tracer or an explicit `tracer` option. Applications must install an OTel context
manager for asynchronous propagation. Importing helpers has no registration effects.

### Generators and streaming

```ts
const streamAnswer = span(
  { name: 'answer', type: 'llm' },
  async function* (query: string) {
    for await (const token of generateTokens(query)) yield token;
    updateSpan({ output: 'Completed answer' });
  },
);
for await (const token of streamAnswer('Hello')) sendToClient(token);
```

Native sync/async generator functions capture their parent at invocation and start
the span on first execution. Each resume activates that context and restores the
consumer context afterward. Completion, terminal errors, and early closure end the
span; a finally block yielding during closure keeps it open until completion.
`next`, `return`, and `throw` are forwarded; async resumes retain native queuing.
Final return values are captured, not individual yields. Set meaningful output
explicitly; no token accumulation is performed by the wrapper.

Unstarted closure creates no span. `for...of` / `for await...of` close iterators on
break; manual consumers must call `return()` when abandoning a stream. There is no
GC completion guarantee. Generator protocol behavior is supported, not native
object identity. Transpiled generator factories that are no longer native generator
functions use ordinary function behavior.

Ordinary functions returning iterators retain call-duration semantics and return
those iterators unchanged. `withSpan` ends when its callback completes, so consume
a returned stream **inside** its callback to trace the full lifetime. Provider
integrations retain their existing streaming behavior.

### Conversation turns

```ts
await turn(
  { name: 'answer', threadId: 'chat-42', turnId: '2', previous },
  async () => {
    updateTrace({ input: query });
    for await (const token of streamAnswer(query)) sendToClient(token);
  },
);
```

`turn` requires `threadId` or `thread.id`, starts a new root trace, and optionally links a valid
previous OTel `SpanContext`. Its default name is `agent turn` and type is `custom`.
Regular scopes preserve their OTel parent. Finish child work before the entry ends
when it needs to update trace fields; no detached work is awaited automatically.
See [`examples/spans.ts`](examples/spans.ts) for a runnable offline application.

## Existing OpenTelemetry applications

Install the processor when constructing your application's provider:

```ts
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { createSpanProcessor } from 'confident-trace/otel';

const provider = new NodeTracerProvider({
  spanProcessors: [createSpanProcessor()],
  // Your resources, sampler, and additional processors remain application-owned.
});
provider.register();

// At application shutdown:
await provider.shutdown();
```

JavaScript OTel SDK 2.x does not support adding processors after provider
construction. `init()` never attaches to or replaces an existing global provider;
it returns an inactive runtime and reports a diagnostic through OTel `diag`.
Package-level `flush()` and `shutdown()` apply only to the package-owned runtime.
The processor factory throws on invalid configuration; `init()` catches setup
failures and returns an inactive runtime with a content-free diagnostic.

## Configuration

`init()` accepts `instrumentations` (`"all"` or an array of integration names), plus camelCase options: `apiKey`, `endpoint`, `protocol`, `headers`,
`timeoutMillis`, `compression`, `exporter`, `resourceAttributes`, `captureContent`,
`maxContentBytes`, and `redact`. The processor factory accepts export options only.
An injected exporter is owned by the resulting processor and bypasses exporter
configuration. Disabled/invalid-before-construction initialization does not consume it.

- Default protocol: `http/protobuf`; default endpoint:
  `https://otel.confident-ai.com/v1/traces`.
- `grpc` requires a configured collector endpoint.
- Explicit options override environment values. Trace-specific `OTEL_EXPORTER_OTLP_TRACES_*`
  values take precedence over generic `OTEL_EXPORTER_OTLP_*` values.
- An explicit HTTP endpoint is a complete traces endpoint. A generic HTTP environment
  endpoint gets `/v1/traces` appended by OTel. Unspecified TLS, compression, and timeout
  settings remain under the standard exporter's control.
- Headers merge in this order: environment headers, `apiKey`/`CONFIDENT_API_KEY`, explicit
  headers. Header names are case-insensitive. An empty `apiKey` suppresses the API-key
  variable but does not remove an authentication header explicitly configured elsewhere.
- `OTEL_SDK_DISABLED=true` prevents initialization, even with explicit options.
- New providers merge standard default/environment resources and explicit attributes,
  with explicit attributes winning. Existing providers keep their configuration.
- **All explicit TypeScript timeouts are milliseconds**, unlike Python's exporter
  timeout argument. `flush()` defaults to 30000 ms; `shutdown()` defaults to 5000 ms.

Initialization is idempotent. Successful shutdown is terminal for the package-owned
runtime in that process; later `init()` calls return that inactive runtime. Use the
application-owned processor path for application-controlled provider lifecycles.

`flush()` reports that processing drained, not remote backend acceptance. Lifecycle
methods resolve `false` on failure or timeout. A timeout stops waiting; underlying
asynchronous cleanup continues. Repeated shutdown awaits the same cleanup operation.
A JavaScript timeout cannot interrupt synchronous code that blocks the event loop.
Finish active work before shutdown.

## Content and conventions

The content policy defaults to enabled with a 16 KiB attribute cap. It
supports synchronous redaction, omits values when redaction fails, and bounds depth,
node count, and encoded size. It never calls getters, `toJSON`, or iterators. Plain
objects/arrays are supported; class instances, proxies, bigint, undefined, and
functions become `[unsupported]`; nonfinite numbers become null. Cycles truncate.
Non-ASCII JSON is escaped for consistent Python/TypeScript byte accounting.

Provider integrations apply these options to captured messages and system instructions.
They do not sanitize or rewrite third-party OTel spans or attributes written
directly by applications. Provider errors mark the span as failed without capturing
error messages or stacks, which may contain request content.

```ts
import {
  SCHEMA_URL,
  ATTR_GEN_AI_INPUT_MESSAGES,
} from 'confident-trace/semconv';
import type { GenAiMessage } from 'confident-trace/semconv';
```

Constants represent the selected GenAI **1.37.0** contract, not full GenAI coverage.
`spec/semconv.json` at the repository root generates native constants in both SDKs.
The repository also shares wire/content fixtures and one Apache-2.0 license.
Neither installed SDK requires the other language or the repository checkout.

## Development and structure

```sh
pnpm install --frozen-lockfile
pnpm check
```

`src/config`, `runtime`, `exporters`, `content`, and `semconv` own their code and types.
Use `@/` imports rooted at `src/`, including type imports. Test helpers use `@test/`.
Parent-relative imports are rejected by ESLint. Builds resolve aliases and bundle
declarations so consumers do not need matching path configuration.

Tests cover units, shared contracts, local HTTP/gRPC transport, lifecycle, and a
packed consumer installed outside the checkout. Type consumer checks cover NodeNext
and bundler resolution. Package checks install from the existing pnpm cache offline.

Run `node scripts/sync-shared.mjs` **from the repository root** after editing shared
conventions or the root license; commit the generated files. `--check` detects drift.
The root `spec/compatibility.md` records TypeScript support; Python support is
documented in `python/docs/compatibility.md`.

## Manual provider integrations

Use these adapters when the startup hook is unsuitable, including bundled apps.
Initialize with `instrumentations: []`; automatic setup above needs none of these
per-integration registration calls.

Install the SDKs you use; all three are optional peers:

```sh
pnpm add openai # or @anthropic-ai/sdk, @google/genai
```

```ts
import OpenAI from 'openai';
import Anthropic from '@anthropic-ai/sdk';
import { GoogleGenAI } from '@google/genai';
import { init } from 'confident-trace';
import { instrumentOpenAI } from 'confident-trace/openai';
import { instrumentAnthropic } from 'confident-trace/anthropic';
import { instrumentGoogleGenAI } from 'confident-trace/google-genai';

const runtime = init({ instrumentations: [], captureContent: false });
const openai = new OpenAI();
const anthropic = new Anthropic();
const google = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
const restoreOpenAI = instrumentOpenAI(openai);
const restoreAnthropic = instrumentAnthropic(anthropic);
const restoreGoogle = instrumentGoogleGenAI(google);

// Existing SDK methods, options, overloads, and response types stay available.
const stream = await openai.responses.create({
  model: process.env.OPENAI_MODEL!,
  input: 'Hello',
  stream: true,
});
for await (const event of stream) console.log(event.type);

restoreOpenAI();
restoreAnthropic();
restoreGoogle();
await runtime.shutdown();
```

Instrumentation modifies only the supplied client's methods and returns an
idempotent restoration function. Repeated instrumentation does not stack; the first
registration owns its options and restoration. Restore before changing options.
Restoration leaves later third-party method replacements intact. Imports have no
instrumentation side effects and do not load any provider SDK.

| Entry point                    | Instrumented SDK methods                                                                           |
| ------------------------------ | -------------------------------------------------------------------------------------------------- |
| `confident-trace/openai`       | `chat.completions.create`, `responses.create`, including `stream: true`                            |
| `confident-trace/anthropic`    | `messages.create` (including streaming), `messages.stream` (iteration, events, and `finalMessage`) |
| `confident-trace/google-genai` | `models.generateContent`, `models.generateContentStream`                                           |

Each inference emits a CLIENT span with GenAI 1.37.0 attributes, integration label,
model, response ID, available usage/finish reasons, and bounded messages. Calls
inherit the active OTel parent; root calls also write Confident trace input/output.
Streaming captures a bounded prefix of text and OpenAI/Anthropic tool arguments;
usage continues after the content limit. Multimodal binary payloads are omitted.

Integrations use `init()` content settings unless explicitly overridden per client.
An override of one content setting preserves the other initialization settings.
For an application-owned OTel provider, pass content settings directly to each
instrumentor; optionally pass `tracer`. No package `init()` is required in that case.
`OTEL_SDK_DISABLED=true` bypasses instrumentation. Finish streams before shutdown.

OpenAI/Anthropic `withResponse()` preserves the data, response, and request ID.
`asResponse()` leaves the body unread and ends the span at receipt of the raw
response, without response content/usage capture. Awaiting the same streaming
promise repeatedly does not end the span or wrap the stream again. Async iterators
are never advanced for inspection; exhaustion, early loop exit, and iteration
failure end their spans. OpenAI/Anthropic explicit stream controller aborts also
end spans. Close or abort abandoned streams explicitly; an unconsumed stream or
unawaited SDK promise can otherwise leave a span open. Anthropic's eager stream
helper ends on its SDK `end`, `error`, or `abort` event.

Not covered: OpenAI `responses.stream`/Chat helper APIs, beta APIs, Google chat
helpers, batch/embedding/realtime APIs, background polling, full multimodal
normalization, or fan-out of streams via `tee()`. Do not install a second provider
instrumentor on the same client. See the root compatibility matrix for tested
JavaScript SDK versions; Python version ranges are independent.

## Manual Vercel AI SDK integration

AI SDK 7 uses the official `@ai-sdk/otel` integration. Supply our tracer to it:

```sh
pnpm add ai @ai-sdk/otel
```

```ts
import { generateText, streamText } from 'ai';
import { OpenTelemetry } from '@ai-sdk/otel';
import { init } from 'confident-trace';
import { createVercelAITracer } from 'confident-trace/vercel-ai';

const runtime = init({ instrumentations: [], captureContent: false });
const telemetry = {
  integrations: [new OpenTelemetry({ tracer: createVercelAITracer() })],
  recordInputs: false,
  recordOutputs: false,
};

await generateText({ model, prompt: 'Hello', telemetry });
const result = streamText({ model, prompt: 'Hello again', telemetry });
for await (const text of result.textStream) console.log(text);
await runtime.shutdown();
```

The tracer uses the package runtime or your application-owned provider. You can pass
`tracer`, `captureContent`, `maxContentBytes`, and `redact` to override its defaults.
Calls keep their existing arguments, return values, tool callbacks, stream helpers,
and parent context. One integration instance can serve concurrent calls.

Agent, step, model, and tool spans carry `confident.span.integration=Vercel AI SDK`.
Model calls are CLIENT spans; orchestration and tool calls are INTERNAL spans.
Known attributes are normalized to the selected GenAI 1.37.0 message contract;
tool inputs/results use Confident span content attributes. Text and tool calls are
captured, while reasoning, binary, and other unsupported message parts are marked.
Raw request headers, arbitrary SDK metadata, and exception messages/stacks are
omitted. Unrelated tracers/spans are unaffected.

The framework owns stream consumption and its buffers. This adapter bounds and
redacts exported attributes; it does not bound memory retained by AI SDK itself.
Large serialized message attributes are replaced with `[truncated]` before JSON
parsing. Finish consuming or abort streams before flushing/shutting down. For
content opt-out, also set AI SDK's `recordInputs`/`recordOutputs` to false to avoid
its upstream content serialization.

Both `OpenTelemetry` and `LegacyOpenTelemetry` from `@ai-sdk/otel` are tested.
AI SDK 5/6's `experimental_telemetry` API is not covered by the current compatibility
range. Embeddings, reranking, image/audio APIs, and other framework signals are not
part of this integration's supported scope. Use this tracer only with the AI SDK
integration; it intentionally retains only the known AI tracing attribute subset.

See the [AI SDK telemetry documentation](https://ai-sdk.dev/docs/ai-sdk-core/telemetry)
for global registration and per-call configuration.

## Manual Mastra integration

```sh
pnpm add @mastra/core @mastra/observability
```

```ts
import { Mastra } from '@mastra/core';
import { Observability } from '@mastra/observability';
import { ConfidentMastraExporter } from 'confident-trace/mastra';

const observability = new Observability({
  configs: {
    default: {
      serviceName: 'my-agent',
      exporters: [new ConfidentMastraExporter({ captureContent: false })],
    },
  },
});
const mastra = new Mastra({ agents, workflows, observability });
// Run your agents/workflows through mastra.
// After all executions and streams finish:
await observability.shutdown();
```

Mastra owns this exporter's lifecycle. It creates its own OTLP processor and does
not register, replace, or shut down the global OTel provider; `init()` is optional.
Transport/exporter/resource options are the same as `init()`. Content settings
inherit package initialization settings when available, with explicit exporter
settings taking precedence. Its resource uses Mastra's configured service name
unless `resourceAttributes['service.name']` explicitly overrides it.

The exporter converts ended Mastra spans into standard OTLP spans, preserving
trace IDs, span IDs, parent IDs, external parent IDs, and original timestamps.
No start-event cache is required, so resumed workflow spans and out-of-order span
completion work. Native Mastra sampling, filters, and span processors run first.
Event spans export with zero duration. Invalid identifiers/timestamps are skipped.
All spans carry `confident.span.integration=Mastra`; model spans include available
usage/model/finish metadata, and tool spans use `execute_tool`. Agent/model messages
are normalized; workflow and tool input/output data use bounded JSON attributes.
Errors retain failure status without exception text. Only tracing events are
exported; logs, metrics, scores, and feedback are not handled.

`flush()` drains this exporter's processor; `shutdown()` closes it exactly once.
These Mastra interface methods return `Promise<void>` and reject with a content-free
error on failure or timeout. Package-level `flush()`/`shutdown()` do not manage this
independent processor. Finish framework work before calling either lifecycle method.
An injected `exporter` transfers ownership to this adapter.

This is an export adapter, not a Mastra execution-context bridge. To parent unrelated
HTTP/database OTel spans under Mastra spans during execution, configure a compatible
Mastra bridge separately. Do not also export the same Mastra spans through another
OTel exporter to the same destination.

See Mastra's [observability interfaces](https://mastra.ai/reference/observability/tracing/interfaces)
for exporter configuration and lifecycle.

## Manual LangChain and LangGraph integration

Pass a handler in the root invocation's `callbacks`; the framework propagates it
through chains, models, tools, retrievers, and graph nodes:

```ts
import { init } from 'confident-trace';
import { ConfidentLangChainCallbackHandler } from 'confident-trace/langchain';
import { ConfidentLangGraphCallbackHandler } from 'confident-trace/langgraph';

const runtime = init({ instrumentations: [], captureContent: false });
const chainTracing = new ConfidentLangChainCallbackHandler();
await chain.invoke(input, { callbacks: [chainTracing] });

const graphTracing = new ConfidentLangGraphCallbackHandler();
for await (const update of await graph.stream(input, {
  callbacks: [graphTracing],
  streamMode: 'updates',
}))
  console.log(update);

// For LangChain createAgent / graph-backed agents:
const agentTracing = new ConfidentLangGraphCallbackHandler({
  rootSpanType: 'agent',
});
await agent.invoke({ messages }, { callbacks: [agentTracing] });
chainTracing.close();
graphTracing.close();
agentTracing.close();
await runtime.shutdown();
```

Both handlers support `invoke` and fully consumed `stream` runs. Use exactly one
of them per invocation. All descendants retain its `LangChain` or `LangGraph`
integration label. Root chains/graphs and nested chains/nodes default to `custom`,
retrievers to `retriever`, model calls to `llm`, and tools to `tool`. Agent callbacks
mark legacy agent runs; set `rootSpanType: 'agent'` for graph-backed agents because
their ordinary chain callbacks do not identify the graph's purpose.

Model messages include normalized text/tool calls and available model/usage/finish
metadata. Chain state, tool values, and retriever documents use bounded content
attributes. Arbitrary callback metadata, binary message parts, and exception text
are omitted. Content options inherit `init()` defaults and can be overridden on a
handler. The adapters use final model results, including accumulated streaming
results supplied by LangChain; they do not retain a second token buffer. The
framework still owns its buffers. Error/cancellation callbacks end spans as errors;
SDK versions that omit a terminal callback require explicit `close()`.

A handler supports concurrent runs and stores at most `maxActiveSpans` (default
4096). Excess runs and descendants of missing parents are skipped. `close()` is
terminal and ends any outstanding spans as errors. Call it after framework work
finishes; it does not flush or shut down OTel. Use `runtime.flush()`/`shutdown()`
or your application's provider lifecycle. Checkpoint resumes create new invocation
spans; this adapter does not reconstruct the prior process's span tree.

Native callback IDs preserve hierarchy within each observed run. Roots inherit the
caller's active OTel parent, but callback hooks do not enclose execution: unrelated
HTTP/database spans inside a model or tool are not automatically parented under
these callback spans. Attach callbacks at the root; do not add another copy at
each child. See [LangGraph streaming](https://docs.langchain.com/oss/javascript/langgraph/streaming).

## Manual OpenAI Agents integration

Register the processor before creating/running agents:

```ts
import { Agent, run, setTraceProcessors } from '@openai/agents';
import { init } from 'confident-trace';
import { ConfidentOpenAIAgentsProcessor } from 'confident-trace/openai-agents';

const runtime = init({ instrumentations: [], captureContent: false });
const tracing = new ConfidentOpenAIAgentsProcessor();
setTraceProcessors([tracing]);
const agent = new Agent({ name: 'Assistant', instructions: 'Be helpful.' });
await run(agent, 'Hello');
await tracing.shutdown();
await runtime.shutdown();
```

`setTraceProcessors` replaces the SDK's existing processors. Use its
`addTraceProcessor(tracing)` instead when you also want its existing export
destinations. Importing this package never changes the SDK's global processors.
Also set the Runner's `traceIncludeSensitiveData: false` when you want the SDK
itself to avoid collecting model/tool content upstream.

The processor converts native workflow, agent, model, function, task/turn,
handoff, guardrail, and custom span callbacks to OTel spans. It supports regular
and streamed Runner executions; model detail depends on the model implementation
emitting generation/response callbacks. Text/tool messages and available usage are
normalized; audio payloads, credentials, arbitrary trace metadata, and error text
are omitted. Native span types remain in `openai.agents.span.type`. Unknown types
are `custom`; handoffs/guardrails/turns are `custom`. All carry the
`OpenAI Agents SDK` integration label.

Native callback IDs determine parentage; OTel generates its own IDs. The root
inherits the active application OTel parent. As with LangChain callbacks, this
processor does not activate spans around application execution. Register it before
trace start; spans without an observed parent/start are skipped. Restoring a trace
from another process does not reconstruct its previous OTel hierarchy. Server-side
Realtime traces do not pass through these local callbacks.

The processor uses `init()` or your application's registered OTel provider. With an
application-owned provider, pass `{ tracer, flush: () => provider.forceFlush() }`.
`forceFlush()` flushes that hook or the Confident runtime. `shutdown()` stops new
spans, ends outstanding spans as errors, and flushes once; it does not shut down
the OTel provider. Complete/abort streams before shutting down. Content controls,
`maxActiveSpans`, and overflow behavior match the LangChain handlers.

See the [OpenAI Agents SDK overview](https://developers.openai.com/api/docs/guides/agents)
for the framework.

## Span types

All package-owned TypeScript integration spans include `confident.span.type`
alongside `confident.span.integration`:

| Type        | Role                                                                             |
| ----------- | -------------------------------------------------------------------------------- |
| `llm`       | Provider and framework model inference                                           |
| `agent`     | Framework agent runs and Vercel generation orchestration                         |
| `tool`      | Framework tool execution                                                         |
| `retriever` | Retrieval operations                                                             |
| `custom`    | Framework workflows, chains, nodes, handoffs, guardrails, turns, and other roles |

The attribute is set at span creation (or Mastra export conversion), so streaming
and failed calls are typed too. OTel kind, GenAI operation, and Mastra's original
`mastra.span.type` remain available. Native application spans are unchanged.
Import `ATTR_CONFIDENT_SPAN_TYPE` from `confident-trace/semconv` to read this field.

## Request scopes and manual model fields

Call `init()` once. `withTracingSuppressed(callback)` suppresses supported
instrumentation in an isolated sync/async request scope. `projectContext({
apiKey }, callback)` selects an isolated project exporter before traced work starts.
Both work without an application wrapper when provider calls are instrumented.
Changing projects within an active span throws. Existing spans retain their route
when the scope exits; errors never fall back to another project. Authentication
keys are never span attributes or baggage.

Custom exporters require `projectExporterFactory(apiKey)` to create separately
owned exporters. Idle routes are capped at 64 after successful cleanup, with
active and queued routes retained. Runtime flush/shutdown include all routes.
Completed spans still export in ordinary batches; there is no late drop flag.
Outcome-based whole-trace dropping requires collector tail sampling, not merely
a metadata field. Independent Mastra exporters, subprocess exporters, and
unrelated pipelines retain their own configuration.

`updateSpan({ model, provider, inputTokenCount, outputTokenCount,
costPerInputToken, costPerOutputToken })` records fields on the active model span.
Equivalent options work on `span` / `withSpan` with `type: 'llm'`. Counts must be
nonnegative integers and rates finite nonnegative USD per token. Zero is retained.
These helpers do not run evaluations or calculate authoritative totals.

`updateTrace({ testCaseId })` emits `confident.trace.test_case_id`.
`updateTrace({ thread: { id, tags, metadata } })` and the same options on `turn`
emit `confident.trace.thread.*`, separate from trace tags/metadata. The ID also
emits legacy `confident.trace.thread_id`. Conflicting `threadId` and `thread.id`
values throw. Supplied thread tags/metadata replace the field within the trace;
omitted fields stay unchanged and metadata obeys content policy. Storage of the
new namespace, cross-trace merging, and AI Connection linkage require receiver
verification; no backend changes are included.

LLM fields on a non-LLM span are skipped with a warning once per incompatible category per process; general
fields still apply. Updates without a recording span remain no-ops. The legacy
`updateLlmSpan` helper remains a compatibility alias.

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

```ts
traceContext({ metricCollection: 'answer-checks', testCaseId: 'case-1', turnId: 'turn-1' }, () => {
  withSpan({ name: 'retrieve', metricCollection: 'retrieval-checks' }, () => {
    updateSpan({ metricCollection: 'updated-retrieval-checks' });
    updateTrace({ metricCollection: 'updated-answer-checks' });
  });
});
```

## LiteLLM proxy

Use the existing OpenAI SDK instrumentation with the exact LiteLLM client base URL:

```ts
import { init } from 'confident-trace';

init({ litellmProxyUrls: ['http://localhost:4000/v1'] });
// Run with node --import confident-trace/register ... as described above.
```

Manual `instrumentOpenAI(client, { litellmProxyUrls: [...] })` also supports this
option. Matching includes the full base path and ignores trailing slashes. Proxy
calls retain the `OpenAI` integration label and add `confident.gateway.name=litellm`
and `gen_ai.provider.name=litellm`. Requested aliases and returned models remain
separate; upstream providers are not inferred from model names. Content capture,
streaming, and error handling follow the existing OpenAI integration.

This instruments application-side Chat Completions/Responses calls. LiteLLM's
native Python SDK is covered by the Python package. Gateway-internal retries,
fallbacks and trace propagation require separate gateway/HTTP OTel setup.

## OpenRouter

With the standard `--import confident-trace/register` preload and `init()`, native
`@openrouter/sdk` chat calls are automatically instrumented. Select
`instrumentations: ['openrouter', 'openai']` to enable just these SDKs.

The tested SDK version (`1.2.116`) uses a `chatRequest` wrapper:

```ts
import { init } from 'confident-trace';
import { OpenRouter } from '@openrouter/sdk';

init();
const client = new OpenRouter({ apiKey: process.env.OPENROUTER_API_KEY });
const result = await client.chat.send({
  chatRequest: {
    model: 'openai/gpt-4o-mini',
    messages: [{ role: 'user', content: 'Hello' }],
  },
});
```

Manual setup is available with `instrumentOpenRouter(client)` from
`confident-trace/openrouter`. It patches the client in place and returns a restore
function. Native `chat.send` supports regular and streaming responses, camelCase
usage and tool-call fields, content controls, and errors. The native SDK is ESM;
CommonJS applications can load it with dynamic `import()`.

OpenAI clients pointed at `https://openrouter.ai/api/v1` are automatically labeled
with `confident.gateway.name=openrouter`. For custom endpoints, pass
`openrouterProxyUrls: ['https://gateway.example/api/v1']` to `init()` or
`instrumentOpenAI()`. Matching uses the exact origin and path, ignoring trailing
slashes. Explicit LiteLLM mappings take precedence over OpenRouter detection.

Native spans use integration `OpenRouter`; gateway calls through OpenAI retain
integration `OpenAI`. Provider name is `openrouter`; requested and returned models
remain separate. Gateway-internal routing, fallback attempts, the Agent SDK,
embeddings, native Responses and functional SDK helpers are outside this scope.

## Portkey

With `node --import confident-trace/register app.mjs` and `init()`, Portkey SDK
calls are automatically instrumented. Use `instrumentations: ['portkey']` to
select just this SDK, or include `'openai'` for OpenAI clients calling a gateway.

```ts
import { init } from 'confident-trace';
import { Portkey } from 'portkey-ai';

init();
const client = new Portkey({ apiKey: process.env.PORTKEY_API_KEY });
const result = await client.chat.completions.create({
  model: 'gpt-4o-mini',
  messages: [{ role: 'user', content: 'Hello' }],
});
```

Manual setup is `instrumentPortkey(client)` from `confident-trace/portkey`;
it returns a restoration function and coexists with automatic instrumentation.
Covers `chat.completions.create` and `responses.create`, including streaming,
usage, tool calls, errors and early iteration exit. Content controls apply as
usual; request arguments, Portkey headers and routing configuration are preserved.
Nested Confident provider spans are suppressed.

OpenAI clients pointed at `https://api.portkey.ai/v1` are identified automatically.
For custom endpoints, pass `portkeyProxyUrls: ['https://gateway.example/v1']` to
`init()` or `instrumentOpenAI()`. Exact origin/path matching ignores trailing
slashes. Overlapping mappings use LiteLLM → OpenRouter → Portkey precedence.

Native spans use integration `Portkey`; OpenAI proxy spans retain `OpenAI` and
add `confident.gateway.name=portkey`. Provider name is `portkey`; requested and
returned models remain separate. Headers, keys and gateway URLs are not exported.
Tested with `portkey-ai==3.1.0`. Gateway-internal routing/retries, prompt-management
APIs, embeddings and separate streaming helpers are outside this integration.

### Bifrost gateway

Use the existing OpenAI or Anthropic instrumentation with explicit Bifrost base URLs:

```ts
import { init } from 'confident-trace';
import OpenAI from 'openai';

const runtime = init({
  bifrostProxyUrls: [
    'http://localhost:8080/openai',
    'http://localhost:8080/anthropic',
  ],
});
const client = new OpenAI({
  baseURL: 'http://localhost:8080/openai',
  apiKey: '<virtual-key>',
});
await client.chat.completions.create({
  model: 'openai/gpt-4o-mini',
  messages: [],
});
await runtime.shutdown();
```

Start Node with `node --import confident-trace/register app.mjs`; automatic SDK
patching requires this preload plus `init()`. For manual setup, pass
`{ bifrostProxyUrls: [...] }` to `instrumentOpenAI` or `instrumentAnthropic`.
Supported surfaces are OpenAI chat completions/Responses and Anthropic Messages,
including streams and the Messages stream helper. Spans retain the SDK integration
label and set `confident.gateway.name` and `gen_ai.provider.name` to `bifrost`.
Matching uses the exact origin and base path, ignoring trailing slashes; there is
no automatic localhost detection. URLs and virtual-key headers are not exported.
This captures application calls, not Bifrost's internal routing, retries, fallbacks
or background inference polling lifecycle. Native Go, GenAI and Bedrock clients
are outside this integration's scope.

### TrueFoundry gateway

Configure the full gateway base URL with `truefoundryProxyUrls`:

```ts
import { init } from 'confident-trace';
import OpenAI from 'openai';
import Anthropic from '@anthropic-ai/sdk';

const baseURL = process.env.TRUEFOUNDRY_GATEWAY_BASE_URL!;
const apiKey = process.env.TRUEFOUNDRY_API_KEY!;
const runtime = init({ truefoundryProxyUrls: [baseURL] });
const openai = new OpenAI({ baseURL, apiKey });
const anthropic = new Anthropic({
  baseURL,
  apiKey,
  defaultHeaders: { Authorization: `Bearer ${apiKey}` },
});
// Call either client normally, using your TrueFoundry model names.
// Shut down after your application has finished its calls:
await runtime.shutdown();
```

Automatic instrumentation requires `node --import confident-trace/register app.mjs`
plus `init()`. For manual setup, pass `{ truefoundryProxyUrls: [baseURL] }` to
`instrumentOpenAI` or `instrumentAnthropic` instead of using the preload.
OpenAI chat completions/Responses and Anthropic Messages, including streams and
the Messages stream helper, retain their SDK integration labels and set
`confident.gateway.name` and `gen_ai.provider.name` to `truefoundry`.
Matching uses the exact origin and base path, ignoring trailing slashes; configure
hosted and custom endpoints explicitly. Client URLs and authentication headers
are not exported. This is application-side tracing; Google GenAI/Bedrock gateway
detection and gateway-internal routing/retries are outside this integration's scope.
