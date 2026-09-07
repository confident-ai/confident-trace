# MEGA: one application, multiple frameworks and execution contexts

This is a runnable stress example using public SDK APIs. By default it starts two
independent request traces concurrently. Each request runs:

- A LangChain prompt → OpenAI model → parser → LangChain tool in a thread pool.
  The tool calls the OpenAI SDK directly, inside a manual `ct.span()`.
- Another direct OpenAI SDK call in the thread pool.
- Pydantic AI in an async task, alongside a streaming OpenAI SDK call.
- OpenAI SDK and Pydantic AI in a spawned process with its own tracing runtime.
- A real Claude Agent SDK `query()` in live mode, with tools disabled and one turn.

The default successful run makes **14 OpenAI model calls and two Claude queries**.
The LangChain tool always executes because it is an explicit chain step; the demo
does not depend on a model deciding to request a tool.

## Install in a separate environment

Run from the repository root. These packages are tested together. Use a dedicated
environment: this Pydantic AI version requires OpenAI 3.x, whereas the tested
LlamaIndex provider in the other examples requires OpenAI <3.

```sh
python3.13 -m venv .venv-mega
source .venv-mega/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ./python -r python/examples/mega/requirements.txt
python -m pip check
```

## First run: no keys or network requests

```sh
python python/examples/mega/mega.py --offline
```

Offline mode executes the real LangChain, OpenAI SDK, Pydantic AI, threads,
processes and OTel pipeline, using deterministic HTTP/SSE responses. It exports
only to memory and **skips Claude**; no CLI session is started. This mode tests
instrumentation and context propagation, not live provider or backend delivery.

The program prints the merged span tree, process IDs and request trace IDs,
checks its invariants, and writes `mega-trace.json` in the current directory.
A successful run ends with `PASS`. The JSON contains model results, span identity,
parentage, scope, status, timestamps and conversation IDs; the audit does not copy
span content attributes or authentication headers.

To force worker reuse and more concurrent requests:

```sh
python python/examples/mega/mega.py --offline \
  --requests 4 --threads 2 --processes 1 --output mega-reuse.json
```

To demonstrate an error while letting all other branches finish:

```sh
python python/examples/mega/mega.py --offline --fail-one --output mega-error.json
```

That command intentionally returns exit code **1**, marks the failed branch and
its request ERROR, and still writes the report. Normal runs return nonzero on a
branch failure or a failed tracing invariant. Exact-count checks assume all spans
are sampled (the default); custom sampling can intentionally change those counts. Run without Python's `-O` flag,
which disables the example's assertions.

## Live run, including Claude Agent SDK

```sh
export CONFIDENT_API_KEY='your-confident-api-key'
export OPENAI_API_KEY='your-openai-api-key'
export OPENAI_MODEL='gpt-4o-mini'
export ANTHROPIC_API_KEY='your-anthropic-api-key'
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT='https://confident-otel-new-us.up.railway.app/v1/traces'
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL='http/protobuf'

python python/examples/mega/mega.py --output mega-live.json
```

Claude's SDK reads `ANTHROPIC_API_KEY` and normally bundles the Claude Code binary;
see its [official setup guide](https://code.claude.com/docs/en/agent-sdk/quickstart).
Set `CLAUDE_MODEL` or pass `--claude-model` if you want a particular available model.
This example uses a temporary working directory with no tools or project settings.

To try live OpenAI/framework tracing before configuring Claude:

```sh
python python/examples/mega/mega.py --skip-claude
```

`--requests 1` gives a smaller live run. SDK HTTP timeouts are 60 seconds, Claude
queries have a 120-second timeout, and configured SDK retries are disabled for the
OpenAI branches. Failed model calls are reported instead of converted into success.

## What the checks establish

The Python audit is an ordinary span processor attached to the same provider as
Confident's exporter. It does not export a second copy. The worker sends only its
local audit records back to the parent; it exports its real spans from the worker.
The program checks:

1. Every observed started span ended exactly once.
2. Independent requests have distinct trace IDs, with no unexpected extra traces.
3. Expected async, thread and process branches parent to the correct request.
4. The SDK call inside the LangChain tool stays below that tool.
5. Each request has exactly seven Python-side inference spans: no lost calls or
   duplicate model spans from layered framework/provider instrumentation.
6. Both native Pydantic AI agent runs are present, and the spawned process shares
   its request's trace and explicit conversation ID.
7. Caller context is restored after sync/async calls and between stream chunks.

All requests use one conversation ID but keep separate traces. Thread submissions
use a fresh `copy_context()`; process submissions carry a W3C `traceparent` string.
Each process owns its own provider/runtime. Workers flush/shut down the runtime
before returning; reused workers retain their application-owned provider.
`spawn` is intentional: this is not a claim that arbitrary initialized SDK state
is safe to inherit with `fork`. Run the file as a script, not pasted into a REPL.

A representative tree, with some intermediate framework spans omitted:

```text
mega.request.0
  thread.langchain
    RunnableSequence
      ChatOpenAI                   # one model span from the LangChain bridge
      sdk_lookup                   # actual LangChain tool execution
        tool.sdk
          chat                     # a distinct direct SDK call
  thread.openai
    chat
  async.pydantic
    invoke_agent
      chat                         # native Pydantic AI model span
  async.openai.stream
    chat
  process.worker                   # same trace, different process
    process.openai
      chat
    process.pydantic
      invoke_agent
        chat
  claude.invocation                # Python invocation scope, live mode only
    ... CLI-native spans ...       # separately exported; inspect in backend
```

## What still needs backend inspection

A local PASS proves Python-side tracing behavior, **not successful backend receipt**.
Use the printed trace IDs to find the same requests in your Railway-backed trace
store. Exporter errors and network/authentication failures may appear in stderr.

Claude's CLI-native spans bypass the Python provider and export independently.
The local audit verifies the Python `claude.invocation` scope and the SDK result;
it cannot assert the CLI's native agent/model/tool spans, their parentage, or their
delivery. Check for native Claude spans below that invocation in the backend.
Their delivery remains unverified here; the script does not fabricate them or
claim its manual invocation scope proves native instrumentation works.

Do not enable another overlapping LangChain/Pydantic/provider instrumentor or an
unreconciled gateway export for this run. W3C propagation joins contexts; it does
not remove duplicate model spans. See [integration boundaries](../../docs/integrations.md)
and [LangChain tracing](../../docs/langchain.md).

## Claude native tracing is experimental

Live runs can emit no native spans or disconnected model roots even when the
query succeeds. The Python audit cannot detect that failure. For one connected
trace per request, keep the Claude call but disable its native telemetry:

```bash
CLAUDE_CODE_ENABLE_TELEMETRY=0 .venv-mega/bin/python python/examples/mega/mega.py
```

This retains `claude.invocation` and gives up native Claude model/tool visibility
and its metrics/logs. It does not disable the Python OpenAI/framework spans.
Do not add another model-span exporter alongside Claude native export as a
workaround. See [known limitation](../../docs/claude-native-tracing.md).

## Claude stream completion and trace names

The example drains Claude's query iterator after receiving its result. The CLI
ends and exports its enclosing `claude_code.interaction` span during completion;
closing immediately at `ResultMessage` can lose that span. A successful Python
audit alone still cannot prove native CLI span delivery. The intended hierarchy is
`claude.invocation` → `claude_code.interaction` → `claude_code.llm_request`, all
with the request's trace ID.

Each default execution creates two independent request traces (`--requests 2`);
two executions create four. Spawned workers preserve W3C parentage and no longer
automatically overwrite the request's trace name with `process.worker`.


## Check Claude native delivery

If the Python wrapper appears in the backend but Claude's native spans do not,
run this from the same shell with your existing export and Claude credentials:

```sh
.venv-mega/bin/python python/examples/mega/check_claude_export.py
```

It makes **one Claude query** (the CLI may make multiple model requests) (no tools or OpenAI calls). A temporary local
receiver forwards Claude's OTLP batches once to your configured collector and
reports span identities, HTTP status, and the collector's rejected-span count.
It does not print request content, headers or credentials, and does not overwrite
`mega-trace.json`. It requires HTTP/protobuf, the default example protocol.

- No receipts: the local receiver saw no native export. The `telemetry` report lists recognized CLI startup/export stages to help distinguish remote-settings waits, initialization errors, and exporter errors. Unknown CLI versions may emit different markers; an empty stage list is inconclusive. Raw debug logs stay in a temporary directory and are deleted after the check; only fixed labels are printed.
- HTTP 401/403: collector access was rejected.
- HTTP 200 with a nonzero rejected count: the collector rejected spans.
- PASS: native spans share the parent trace and the collector acknowledged them;
  verify storage using the printed trace ID.

The last network hop uses Python HTTP rather than the CLI's HTTP client. A PASS
does not certify direct CLI TLS/proxy connectivity, nor does an HTTP acknowledgement
prove storage. This is a diagnostic route, not an additional export pipeline or
a production integration. Normal MEGA runs continue exporting directly.
