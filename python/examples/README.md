# Try Confident Trace locally

Run these commands from the repository root. Python 3.13 is a tested choice.
Install the SDK from this checkout so you get the integrations implemented here:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ./python
```

Use `deactivate` to leave the environment. In a new terminal, activate it again
before running examples. Framework packages belong to your application; installing
Confident Trace alone does not install them.

Want a combined concurrency demo? See [MEGA](mega/README.md): LangChain, direct
OpenAI, Pydantic AI, Claude Agent SDK, async streaming, thread pools, and spawned
processes, with a merged trace report and executable parentage checks.

## 1. See a trace without API keys

```sh
python python/examples/local_trace.py
```

This prints `HELLO` and two OTel spans as JSON: `request` and its `execute_tool
lookup` child. They have the same trace ID and explicit parentage, plus input,
output and conversation attributes. This example uses a console exporter and
makes no provider or backend requests.

## 2. Send a real model trace to Confident

```sh
python -m pip install openai
export CONFIDENT_API_KEY='your-confident-api-key'
export OPENAI_API_KEY='your-openai-api-key'
export OPENAI_MODEL='gpt-4o-mini'  # Or a model available to your account.
python python/examples/openai/chat_completions.py
```

The model response prints in your terminal. The trace is exported to Confident's
OTLP endpoint at `https://confident-otel-new-us.up.railway.app/v1/traces`;
inspect it in the Confident project associated with your API key.
The provider example produces a model span without needing `@span`. Framework
examples additionally demonstrate agent, workflow and tool structure. A model
chooses whether to invoke a tool; a successful response need not include every
possible tool span.

`OPENAI_MODEL` is respected by the OpenAI-backed framework examples too. They call
live provider APIs. Examples finish or flush tracing before normal process exit.

Standard `OTEL_EXPORTER_OTLP_*` settings override the default destination. If you
already use a collector, keep those settings. To try a local HTTP collector instead:

```sh
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT='http://localhost:4318/v1/traces'
export OTEL_EXPORTER_OTLP_PROTOCOL='http/protobuf'
python python/examples/openai/chat_completions.py
```

This assumes a collector is listening there; it does not start one. To return to
the default Confident destination, remove custom endpoint settings, including any
base `OTEL_EXPORTER_OTLP_ENDPOINT` you previously configured. See
[environment configuration](environment.py) and [existing provider ownership](existing_provider.py).

## 3. Try a framework

After the base installation and API-key setup above, install the packages in the
second column. All commands below run from the repository root in your activated
environment. Start with one framework; use a separate environment for a different
tested dependency set if its version requirements conflict.

| Framework | Install command | Run command |
|---|---|---|
| LangChain chain, streaming | `python -m pip install langchain-openai` | `python python/examples/langchain_chain.py` |
| LangChain agent and tool, async | `python -m pip install langchain langchain-openai` | `python python/examples/langchain_agent.py` |
| LangGraph agent, tools and conversation memory | `python -m pip install langgraph langchain-openai` | `python python/examples/langgraph_agent.py` |
| LlamaIndex agent and tool | `python -m pip install llama-index-core llama-index-llms-openai` | `python python/examples/llamaindex_agent.py` |
| Agno agent and tool | `python -m pip install agno openai` | `python python/examples/agno_agent.py` |
| smolagents tool-calling agent | `python -m pip install 'smolagents[openai]'` | `python python/examples/smolagents_agent.py` |
| CrewAI crew/task/agent | `python -m pip install crewai` | `python python/examples/crewai_agent.py` |
| Pydantic AI | `python -m pip install 'pydantic-ai-slim[openai]'` | `python python/examples/pydantic_agent.py` |
| Strands with OpenAI | `python -m pip install 'strands-agents[otel,openai]'` | `python python/examples/strands_agent.py` |
| Microsoft Agent Framework | `python -m pip install agent-framework-core agent-framework-openai` | `python python/examples/microsoft_agent_framework.py` |
| OpenAI Agents SDK | `python -m pip install -e './python[openai-agents]' openai-agents` | `python python/examples/openai_agent.py` |

For the three newest integrations, this installs the versions tested together:

```sh
python -m pip install -c python/tests/constraints/frameworks.txt \
  llama-index-core llama-index-llms-openai agno 'smolagents[openai]'
python python/examples/llamaindex_agent.py
python python/examples/agno_agent.py
python python/examples/smolagents_agent.py
```

Additional streaming examples use those same dependencies and keys:

```sh
python python/examples/llamaindex_streaming.py
python python/examples/agno_streaming.py
python python/examples/smolagents_streaming.py
```

LlamaIndex consumes workflow events and then awaits completion. Agno demonstrates
an async stream. smolagents demonstrates typed events and final output; its
upstream early-close exception is described in the [framework guide](../docs/frameworks.md).
Keep request scopes around consumption and close streams when stopping early.

The LangGraph example makes two turns using one in-memory checkpoint session.
Expect two request traces associated with `example-conversation`, not one reused
trace ID. To inspect a graph without keys or model calls, run:

```sh
python -m pip install langgraph
python python/examples/langgraph_local.py
```

That example prints span IDs and parent relationships locally; it does not export
them to Confident. Frameworks with structural adapters use existing provider
adapters for model spans. Do not add a second overlapping framework instrumentor
or unreconciled gateway model export. See [tracing ownership](../docs/frameworks.md),
[LangChain/LangGraph details](../docs/langchain.md), and the
[tested compatibility matrix](../docs/compatibility.md). Tested dependency pins are
in [tests/constraints](../tests/constraints); they are not runtime version gates.

## Other model providers

Install `anthropic`, `google-genai`, or `boto3` alongside Confident Trace.

| Provider | Environment variables |
|---|---|
| Anthropic | `CONFIDENT_API_KEY`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| Google GenAI | `CONFIDENT_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_MODEL` |
| AWS Bedrock | `CONFIDENT_API_KEY`, `AWS_DEFAULT_REGION`, `BEDROCK_MODEL_ID`; normal AWS credentials/profile |

| Provider / API | Sync | Async |
|---|---|---|
| OpenAI Responses | [responses.py](openai/responses.py) | [async_responses.py](openai/async_responses.py) |
| OpenAI Chat Completions | [chat_completions.py](openai/chat_completions.py) | [async_chat_completions.py](openai/async_chat_completions.py) |
| OpenAI Responses streaming | [streaming.py](openai/streaming.py) | [async_streaming.py](openai/async_streaming.py) |
| Anthropic Messages | [messages.py](anthropic/messages.py) | [async_messages.py](anthropic/async_messages.py) |
| Anthropic Messages streaming | [streaming.py](anthropic/streaming.py) | [async_streaming.py](anthropic/async_streaming.py) |
| Google GenAI content | [generate_content.py](google_genai/generate_content.py) | [async_generate_content.py](google_genai/async_generate_content.py) |
| Google GenAI streaming | [streaming.py](google_genai/streaming.py) | [async_streaming.py](google_genai/async_streaming.py) |
| Bedrock Converse | [converse.py](bedrock/converse.py) | [async_converse.py](bedrock/async_converse.py), thread offload |
| Bedrock streaming | [streaming.py](bedrock/streaming.py) | — |

For example:

```sh
python -m pip install anthropic
export ANTHROPIC_API_KEY='your-anthropic-api-key'
export ANTHROPIC_MODEL='your-model-id'
python python/examples/anthropic/messages.py
```

Boto3 is synchronous; cancellation of the asyncio example does not stop its worker
request. Async examples use `asyncio.run(main())`; in an existing event loop, use
`await main()`. [Conversation metadata](conversation.py) demonstrates explicit
association of separate request traces.

## Google ADK, AgentCore, and Claude

- **Google ADK:** `python -m pip install google-adk`; set `GOOGLE_API_KEY`,
  `CONFIDENT_API_KEY`, and optionally `GOOGLE_MODEL`. Run
  `python python/examples/google_adk/agent.py` for an agent, tools and streaming.
- **AgentCore with Bedrock:** `python -m pip install -e './python[agentcore]' bedrock-agentcore boto3`;
  set `BEDROCK_MODEL`, `CONFIDENT_API_KEY`, and normal AWS credentials/region. Run
  `python python/examples/agentcore/bedrock.py`.
- **AgentCore with Strands:** additionally install `strands-agents[otel]`; use the
  same environment and run `python python/examples/agentcore/strands.py`.
- **Claude Agent SDK:** `python -m pip install claude-agent-sdk`; authenticate Claude
  and set `CONFIDENT_API_KEY` or OTLP settings. Run
  `python python/examples/claude_agent.py`. Native CLI traces require beta telemetry
  support; native span delivery remains unverified, as explained in the
  [integration guide](../docs/integrations.md).

AgentCore examples serve port 8080 locally and do not deploy AWS resources. From
another terminal:

```sh
curl http://localhost:8080/invocations \
  -H 'Content-Type: application/json' \
  -H 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: 12345678-1234-1234-1234-123456789012' \
  -d '{"prompt":"Hello"}'
```

An optional `traceparent` header continues an existing distributed trace. Hosted
telemetry setup, native content policies, and backend interpretation have separate
boundaries described in the integration guide.
