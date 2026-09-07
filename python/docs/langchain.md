# LangChain and LangGraph

The in-house bridge creates ordinary OpenTelemetry spans using Confident Trace's
existing provider and exporter. It emits the pinned GenAI 1.37.0 attributes; it
does not use OpenInference, LangSmith export, or DeepEval's trace model.

`init()` automatically instruments installed LangChain Core and optionally
LangGraph. Explicit `instrumentations=("langchain",)` or `("langgraph",)` selects
the same bridge, including graph context support when installed. Applications
supply their own framework/provider packages. No new public callback API is needed.
Existing callbacks and app-owned OTel processors remain installed.

## Hierarchy and content

Callback `run_id` and `parent_run_id` determine framework hierarchy. Root callbacks
inherit the active OTel context at execution time, including a surrounding
`ct.span("request")`. Every intermediate callback run is retained. Chat and text
models receive CLIENT spans with `chat`/`text_completion`; tools receive INTERNAL
`execute_tool` spans. Explicit `run_type="agent"` callbacks receive `invoke_agent`.
Ordinary graphs, nodes, chains, parsers, and retrievers remain INTERNAL spans
without fabricated GenAI operations. Names alone do not identify agents.

Tool requests appear in the model's output messages. Tool executions follow their
actual framework parent; the last model span is never used as an implicit parent.
`metadata.thread_id` (including LangGraph's checkpoint thread metadata) becomes
`gen_ai.conversation.id`. Runs sharing that ID still get separate traces unless an
application explicitly supplies a common active parent. Interrupt/resume is normal
control flow and each resumed graph invocation gets a new root span.

Supported model surfaces include invoke/ainvoke, batch/abatch, stream/astream and
astream_events v2. The newer protocol stream_events v3 surface is not certified.

Model callbacks normalize text, tool calls/responses, finish reasons, model/provider
identifiers and available usage. Unknown multimodal blocks are omitted rather than
serialized indiscriminately. Generic run inputs/results and document text use
existing Confident content fields. Every content field passes through the same
redaction, enablement and size policy. Arbitrary configuration, callback objects,
checkpoint internals, and document metadata are not captured. Token usage remains
available with content disabled. Streaming uses the framework's final aggregated
result; the bridge does not buffer another copy of every token.

## Concurrency and streams

A runtime-owned registry maps run IDs to spans, protected by a short-held lock.
It does not store one mutable current trace for all requests. Execution hooks
activate context in framework config scopes, model run-manager scopes, and
iterator-local stream scopes. A manual or third-party OTel span created inside a
supported node/tool/model inherits that operation. Context tokens are restored in
the same execution context, never by a later callback running in another task.

Framework-managed batch/thread pools and async tasks carry their usual contexts.
For application-owned thread pools, propagate the active context at submission:

```python
from contextvars import copy_context

future = pool.submit(copy_context().run, graph.invoke, state, config)
```

Use a fresh copy for every submission. Passing the inherited RunnableConfig also
preserves callback parent IDs, but cannot reconstruct an unrelated OTel request
context that was never propagated. On Python 3.10, pass RunnableConfig explicitly
to nested async framework calls when required by the framework. No global Python
threading or executor patch is installed.

Consume or explicitly close streams (`close()` / `aclose()`). The model stream's
context is active only while advancing or closing its iterator, not between chunks.
Callback completion, errors, cancellation and explicit closure end spans once.
Cancellation cannot stop synchronous work already running in a worker; that work
may finish after its parent and retains its captured trace context. Shutdown closes
remaining owned operations and removes owned hooks without shutting down the app's
provider. An abandoned, never-closed async stream is not promised prompt cleanup.

Our provider wrapper is suppressed only while an owned model operation is active.
A direct provider call from a tool or node still creates a model span. Custom
framework implementations bypassing both callbacks and standard execution helpers
are outside the automatic nesting guarantee; direct provider instrumentation remains
available. An LLM backend processing multiple prompts in one batch uses the first
run manager for shared backend execution, matching LangChain's helper contract;
callbacks still represent each input separately.

## Compatibility and deployment

The tested 1.x versions are pinned in `tests/constraints/langchain.txt`, with a
separate latest-dependencies CI job. Pins document tested versions rather than
runtime restrictions. Narrow context hooks depend on framework implementation
helpers; unknown/missing targets are skipped, and unresolved model ownership does
not suppress provider instrumentation. Existing third-party wrappers are preserved.

Remote graph servers must initialize tracing in their own process. A callback
installed in a client cannot observe execution inside a remote deployment. Backend
mapping and real collector delivery are separate from these offline SDK tests.
