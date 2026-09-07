# CrewAI

Install CrewAI and the model provider packages your application uses. There is no
`confident-trace[crewai]` runtime extra and no OpenInference dependency.

```python
import confident_trace as ct

ct.init()  # Includes CrewAI and installed provider adapters.
with ct.span("request", thread_id="conversation-123"):
    result = crew.kickoff()
ct.flush()
```

For explicit selection with an OpenAI-backed crew:
`ct.init(instrumentations=("crewai", "openai"))`. Selecting only `crewai` captures
execution structure; it does not implicitly select provider adapters.

## Span ownership and hierarchy

The in-house adapter creates named INTERNAL crew, task, flow, and flow-method
spans. Agent execution uses `invoke_agent`; tool execution uses `execute_tool`
with its name and an explicit native tool-call ID when available. Generic inputs
and outputs use the existing `confident.*` attributes and content policy. It does
not serialize agent configuration, credentials, memory stores, or checkpoint
state. Explicit thread IDs associate conversations without reusing trace IDs.

Model spans belong to the existing provider adapters. Their normalized messages,
tool requests, usage, and finish reasons follow our pinned GenAI 1.37.0 contract.
There is **no additional CrewAI LLM span** and no CrewAI-wide provider suppression.
The internal agent Flow is preserved too. A typical hierarchy is:

```text
request
  crew
    task
      invoke_agent
        flow AgentExecutor
          node call_llm_native_tools
            chat                 # model requests a tool
          node execute_native_tool
            execute_tool         # separate branch, never a child of chat
              custom span
              chat               # direct provider call made by the tool
          node call_llm_native_tools
            chat                 # model consumes tool results
```

This distinction matters because CrewAI's `LLM.call` can both call a provider and
execute tools. Wrapping the entire method as a model span would conflate them.
Unsupported/custom LLM implementations receive no synthetic fallback model span;
use a supported provider SDK or explicit manual instrumentation. LiteLLM paths
that bypass our supported provider methods are not certified.

Do not combine this adapter with another CrewAI structural instrumentor. Skipping
preexisting wrapt wrappers is an ownership safeguard, not universal duplicate
detection for event listeners or instrumentation enabled later. Choose one
instrumentor. Likewise, gateway-exported model spans and local model spans still
require the single-owner policy in the [roadmap](../ROADMAP.md#ai-gateways-export-propagation-and-client-coverage).
If both sources cannot be reconciled, disable one overlapping export path.
CrewAI's own tracing/analytics settings remain application-owned; this integration
does not enable its hosted tracing service.

## Context, concurrency, and streams

Execution scopes make ordinary OTel spans and `ct.span()` children of the current
agent, tool, or flow method. They restore context in the same thread/task in
`finally`. There is no event-listener start/end attachment or shared current-run
field. CrewAI 1.15.20 copies context for task threads, timed agents, native parallel
tools, and synchronous flow methods. We rely on that upstream behavior instead of
patching Python threads or executors globally.

`CrewStructuredTool.ainvoke` has one additional invocation-local binding: its
synchronous `func` otherwise executes through an executor lambda without context
propagation. A transparent proxy carries the copied context into that function;
the shared tool, usage counters, validation, return values and exception behavior
remain owned by CrewAI.

Application-created pools need explicit context copying at submission:

```python
from contextvars import copy_context

with ct.span("request"):
    future = pool.submit(copy_context().run, crew.kickoff)
    result = future.result()
```

Crew streaming returns a handle before starting execution. The single crew span
belongs to the inner execution, under the context active when consumption begins.
Keep the request scope around consumption and close/aclose streams when stopping
early. Context is not attached between chunks. A synchronous worker can continue
after an async caller is cancelled; its spans end when that worker finishes.
Human-feedback pauses close normally; resume creates a new invocation under its
current request context. Shutdown closes remaining owned spans once and restores only owned hooks.

## Scope and compatibility

Targeted hooks cover `Crew.kickoff` / native `akickoff`, task execution cores,
`Agent.execute_task` / `aexecute_task`, standalone agent execution/output helpers,
`Flow.kickoff_async` / `resume_async` / `_execute_method`, and tool run/invoke methods. Async
convenience methods delegate to those boundaries and do not add duplicate spans.
Private hooks are deliberate and capability-checked, with tested versions recorded
in constraints rather than runtime version gating. Missing hooks fail open;
provider instrumentation remains available.

Remote AMP execution requires instrumentation inside its worker process. Memory
operations, embeddings, hosted export, arbitrary overridden execution methods,
and older releases' thread behavior are outside the verified coverage.

Design reference: OpenInference's [CrewAI instrumentor](https://arize-ai.github.io/openinference/python/instrumentation/openinference-instrumentation-crewai/)
and [wrapper targets](https://github.com/Arize-ai/openinference/blob/main/python/instrumentation/openinference-instrumentation-crewai/src/openinference/instrumentation/crewai/__init__.py).
The implementation here uses our runtime and policy; it does not copy their span
model or add an event bus listener.
