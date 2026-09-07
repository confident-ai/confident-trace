# LlamaIndex, Agno, and smolagents

These in-house integrations use the existing Confident Trace runtime and content
policy. Applications install their framework and model provider packages; there
are no framework runtime extras or OpenInference dependencies.

```python
import confident_trace as ct

ct.init()  # Includes installed frameworks and provider adapters.
with ct.span("request", thread_id="conversation-123"):
    result = agent.run("Explain the result")
ct.flush()
```

For asynchronous APIs, await the run inside that scope. For streaming, consume and
close the iterator there. Explicit selection uses `llamaindex`, `agno`, or
`smolagents`, together with the provider adapters you need, for example
`ct.init(instrumentations=("agno", "openai"))`.

## One owner for each model call

Framework adapters emit execution structure: `invoke_agent` for agents,
`execute_tool` for tools, and named INTERNAL spans for workflow/retrieval/step
activity. Existing provider adapters own inference spans, normalized messages,
usage, finish reasons and model tool requests under our pinned GenAI 1.37.0
contract. Framework adapters do not create a second model span or suppress direct
provider calls made inside tools.

```text
request
  invoke_agent
    step / workflow node
      chat                 # model requests a tool
      execute_tool         # actual execution, never parented to the last chat
        custom span
        chat               # a separate provider call inside this tool
    step / workflow node
      chat                 # next model request
```

Exact intermediate nodes depend on the framework. Generic data uses the existing
`confident.*` content attributes with redaction and size limits. Conversation IDs
associate invocations; they never cause trace IDs to be reused.

Custom/local models and model libraries that bypass supported provider methods
receive no synthetic fallback inference span. Instrument those model calls
explicitly or select a supported provider SDK. Embeddings remain outside this
coverage. Selecting only a framework does not implicitly select its providers.

**Choose one structural instrumentor per framework.** Do not combine these
adapters with overlapping OpenInference, OpenLLMetry, or vendor instrumentation.
Preserving existing wrappers/listeners does not reconcile their spans. Likewise,
W3C propagation does not deduplicate gateway and SDK model telemetry: follow the
[single-owner gateway policy](../ROADMAP.md#ai-gateways-export-propagation-and-client-coverage)
and disable one overlapping path when reconciliation is unavailable. We do not
enable, disable or redirect the frameworks' hosted telemetry services.

## LlamaIndex

The adapter listens to the native instrumentation dispatcher for explicit span
IDs and parents. It binds OTel context inside native dispatcher-decorated
functions, including definitions loaded before initialization and decorators
created later. Native LLM spans are omitted so provider calls have one owner.
Tools use `call`/`acall`; delegating `__call__` does not create another tool span.
Agent workflows, retrieval, and other dispatcher activity retain their hierarchy.

Callbacks only manage span lifecycle; they never attach context for a later
callback to detach. Generator spans remain open until consumption ends or the
iterator is closed. Iterator driving restores both OTel context and the native
span ID, so nested dispatcher activity has the right parent. Workflow run handles
retain their native identity and cancellation API; root spans follow dispatcher
completion rather than the method that returns the handle.

The narrow private dependency is the native `Dispatcher.span` wrapt decorator.
The adapter inserts a scope underneath that decorator and restores only its owned
inner wrapper. Loaded framework definitions and `DispatcherSpanMixin` subclasses
are scanned without evaluating properties. Arbitrary standalone decorators in
user modules loaded before initialization are not covered by that scan; initialize
before defining them. Remote workflow runtimes require separate instrumentation
and propagation in their worker processes. We do not serialize workflow context,
checkpoint state, broker state or configuration to invent remote propagation.

## Agno

Agent and team `run`/`arun`, workflow execution, step/parallel/condition/loop/router
boundaries and `FunctionCall.execute`/`aexecute` receive scopes. The public async
run dispatcher may return either a coroutine or an async iterator; both forms are
handled. Function-call spans include an available call ID, and explicit session
IDs become `gen_ai.conversation.id`. Error results are marked ERROR even when Agno
returns a failure object instead of raising.

Generator tools defer completion until their returned result iterator is driven
or closed. Both invocation-local references to that result retain the same proxy.
Background dispatch is excluded: enqueueing a background job is not a completed
agent execution. Background/remote workers need instrumentation at their actual
execution boundary; full background-job lifecycle is not certified here.

## smolagents

Scopes cover the agent run generator, planning, tool/code steps, and `Tool.__call__`.
The latter covers tools executed by both `ToolCallingAgent` and local `CodeAgent`.
Tool IDs are not exposed at that shared boundary and are omitted instead of
inferred from mutable agent history. Recovered step errors do not automatically
mark the eventual successful agent run as failed.

The SDK's early-close path can raise `RuntimeError: generator ignored GeneratorExit`
because its run generator yields an action step from `finally`. We preserve that
upstream exception and close our own spans. There is no native async agent API in
the tested release; applications can use context-propagating worker threads.
Remote code executors/sandboxes need instrumentation in the remote process.

## Concurrency and lifecycle

Scopes are invocation-local. Locks cover bookkeeping, not application work,
awaits or export. Context is restored in the same task/thread and is inactive
between stream chunks. We rely on each framework's context propagation for its
workers, without globally patching threading. Application-created pools need a
fresh context copy per submission:

```python
from contextvars import copy_context

with ct.span("request"):
    result = pool.submit(copy_context().run, agent.run, "Question").result()
```

Close or asynchronously close streams when stopping early. A cancelled async
caller cannot stop a synchronous worker; worker-owned spans may finish later.
Shutdown closes remaining owned spans once and removes only owned hooks/handlers.
Missing or unknown hooks fail open, leaving otherwise available provider tracing
intact. Tested versions are recorded in constraints, not used as runtime gates.

Primary references: [LlamaIndex instrumentation](https://developers.llamaindex.ai/python/framework/module_guides/observability/instrumentation/),
[Agno source](https://github.com/agno-agi/agno), and
[smolagents tracing](https://huggingface.co/docs/smolagents/tutorials/inspect_runs).
