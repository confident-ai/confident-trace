# Tracing

This reference covers SDK initialization, custom spans, trace context, content
controls, and lifecycle. Prefer a supported integration before adding custom
instrumentation.

## Python

Install and initialize before traced provider or framework calls:

```bash
pip install confident-trace
export CONFIDENT_API_KEY="<project-api-key>"
```

```python
import confident_trace as ct

ct.init()
```

`init()` detects supported installed integrations. Restrict the set with
`ct.init(instrumentations=("openai", "langchain"))`. Initialize before
importing cached function aliases that an integration must wrap.

Use custom spans only for application-owned boundaries an integration cannot
see:

```python
import confident_trace as ct

@ct.span(type="retriever")
def retrieve(query: str) -> list[str]:
    documents = search(query)
    ct.update_span(input=query, retrieval_context=documents)
    return documents

with ct.span("answer", type="agent", input=user_input):
    documents = retrieve(user_input)
    answer = generate_answer(user_input, documents)
    ct.update_span(output=answer, retrieval_context=documents)
    ct.update_trace(
        input=user_input,
        output=answer,
        tags=["support"],
        user_id=user_id,
        thread_id=thread_id,
    )
```

`span` works as a decorator and as a synchronous or asynchronous context
manager. Valid types are `agent`, `llm`, `retriever`, `tool`, and `custom`.

Use `turn()` when each conversation turn should be a fresh trace associated
with one thread:

```python
with ct.turn(thread_id="chat-42", turn_id="2", input=user_input):
    answer = run_agent(user_input)
    ct.update_trace(output=answer)
```

Use `trace_context(**fields)` to apply trace defaults without creating a
synthetic span. Use `suppress_tracing()` for request-scoped suppression and
`project_context(api_key=...)` for request-scoped project routing. Do not
switch projects inside an active span.

## TypeScript and JavaScript

Install the SDK:

```bash
npm install confident-trace
export CONFIDENT_API_KEY="<project-api-key>"
```

Start Node with the registration preload, then call `init()` once in the entry
file:

```bash
node --import confident-trace/register dist/index.js
node --import tsx --import confident-trace/register src/index.ts
```

```typescript
import { init } from "confident-trace";

const tracing = init();
```

Both the preload and `init()` are required for automatic instrumentation. Use
`init({ instrumentations: ["openai", "langchain"] })` to restrict integrations,
or `instrumentations: []` for custom/manual-only instrumentation.

Use `span()` for reusable traced functions and `withSpan()` for immediate
scopes:

```typescript
import { span, updateSpan, updateTrace, withSpan } from "confident-trace";

const retrieve = span(
  { name: "retrieve", type: "retriever" },
  async (query: string) => {
    const documents = await search(query);
    updateSpan({ input: query, retrievalContext: documents });
    return documents;
  },
);

const answer = await withSpan({ name: "answer", type: "agent" }, async () => {
  updateTrace({ input: query, userId, threadId, tags: ["support"] });
  const documents = await retrieve(query);
  const output = await generateAnswer(query, documents);
  updateSpan({ output, retrievalContext: documents });
  updateTrace({ output });
  return output;
});
```

TypeScript has no `@span` decorator and no `updateCurrent*` aliases. Use
`turn({ threadId, turnId }, callback)` for fresh conversation-turn traces,
`traceContext(fields, callback)` for defaults, `withTracingSuppressed(callback)`
for suppression, and `projectContext({ apiKey }, callback)` for project routing.

The preload cannot instrument provider code bundled by Webpack, Vite, or
another bundler. In bundled applications, initialize with
`instrumentations: []` and use the matching package subpath adapter documented
in the repository.

## Tags and Metadata

Use trace tags for grouping labels and trace metadata for request, session, or
application context. Use span metadata for component facts such as index,
top-k, tool name, planner route, prompt version, or parser mode. Ask before
adding inferred user sentiment, intent, customer tier, or other fields not
already obvious from the code.

Never record API keys, credentials, or raw sensitive data. Set
`capture_content=False` in Python or `captureContent: false` in TypeScript when
package-owned content must not be captured. Native framework and third-party
instrumentation retain their own content policies.

## Configuration and Lifecycle

- `CONFIDENT_API_KEY`: project API key.
- `CONFIDENT_OTEL_ENDPOINT`: complete traces endpoint for EU, self-hosted, or
  collector export.
- `OTEL_SDK_DISABLED=true`: disable initialization.
- `OTEL_RESOURCE_ATTRIBUTES`: standard OpenTelemetry resource attributes.
- `OTEL_EXPORTER_OTLP_PROTOCOL`: `http/protobuf` by default; use `grpc` only
  with a compatible collector or custom endpoint.

Explicit arguments override environment settings. Python exporter timeouts
passed to `init(timeout=...)` are seconds; `flush()` and `shutdown()` budgets
are milliseconds. TypeScript timeout values are milliseconds.

Finish active work and streams before flushing or shutting down. Servers should
shut tracing down in their existing graceful-shutdown path.

If the application needs raw vendor-neutral OpenTelemetry export without the
package, use the `confident-otel` skill.
