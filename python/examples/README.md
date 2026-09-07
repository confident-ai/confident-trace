# Python examples

Run from the repository root. Install Confident Trace and the provider you use:

```sh
pip install -e ./python
pip install openai        # or: anthropic / google-genai / boto3
export CONFIDENT_API_KEY='your-confident-api-key'
```

Set the provider's API key and a model ID available to your account:

| Provider | Environment variables |
|---|---|
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` |
| Anthropic | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| Google GenAI | `GOOGLE_API_KEY`, `GOOGLE_MODEL` |

OpenAI, Anthropic, and Google GenAI examples have synchronous and asynchronous versions:

| Provider / API | Sync | Async |
|---|---|---|
| OpenAI Responses | [responses.py](openai/responses.py) | [async_responses.py](openai/async_responses.py) |
| OpenAI Chat Completions | [chat_completions.py](openai/chat_completions.py) | [async_chat_completions.py](openai/async_chat_completions.py) |
| OpenAI Responses streaming | [streaming.py](openai/streaming.py) | [async_streaming.py](openai/async_streaming.py) |
| Anthropic Messages | [messages.py](anthropic/messages.py) | [async_messages.py](anthropic/async_messages.py) |
| Anthropic Messages streaming | [streaming.py](anthropic/streaming.py) | [async_streaming.py](anthropic/async_streaming.py) |
| Google GenAI content | [generate_content.py](google_genai/generate_content.py) | [async_generate_content.py](google_genai/async_generate_content.py) |
| Google GenAI streaming | [streaming.py](google_genai/streaming.py) | [async_streaming.py](google_genai/async_streaming.py) |

For example:

```sh
export OPENAI_API_KEY='your-openai-api-key'
export OPENAI_MODEL='your-model-id'
python python/examples/openai/responses.py
# Async equivalent:
python python/examples/openai/async_responses.py
```

Each example calls `init()` and then uses the ordinary provider SDK. Request,
response, and usage capture is automatic. The SDK flushes on normal process exit.
Async examples use `asyncio.run(main())`; in an existing event loop, use `await main()`.
Streaming examples consume the returned stream; no manual trace updates are needed.

Additional examples: [conversation metadata](conversation.py),
[environment/Collector configuration](environment.py), and
[an existing OTel provider](existing_provider.py).

See the [compatibility matrix](../docs/compatibility.md) for exact supported SDK
versions and API coverage. These examples call live provider APIs when run.

## AWS Bedrock Runtime

Install `boto3`. Set `CONFIDENT_API_KEY`, `AWS_DEFAULT_REGION`, and
`BEDROCK_MODEL_ID` (a model ID or inference-profile identifier enabled for your
account). Use the normal AWS credential chain, such as a profile or workload role.

- [Converse](bedrock/converse.py)
- [Converse streaming](bedrock/streaming.py)
- [Asyncio thread offload](bedrock/async_converse.py)

Boto3 is synchronous. The asyncio example offloads a Boto3 call to a worker thread;
it is not native async instrumentation. Native async clients and AgentCore are
separate integrations. Cancelling the await does not cancel the worker's request.


## Native frameworks

- [OpenAI Agents SDK](openai_agent.py): install `confident-trace[openai-agents]`
  and `openai-agents`; set `OPENAI_API_KEY` and `CONFIDENT_API_KEY`.
- [Claude Agent SDK](claude_agent.py): install `confident-trace` and
  `claude-agent-sdk`; authenticate Claude and configure `CONFIDENT_API_KEY` or OTLP.

- [Pydantic AI](pydantic_agent.py): install `pydantic-ai-slim[openai]` alongside
  Confident Trace; set `OPENAI_API_KEY` and `CONFIDENT_API_KEY`.
- [Strands](strands_agent.py): install `strands-agents[openai]` alongside
  Confident Trace; set `OPENAI_API_KEY` and `CONFIDENT_API_KEY`.

- [Google ADK agent, tools, and streaming](google_adk/agent.py):
  `pip install -e './python' google-adk`; set `GOOGLE_API_KEY`, `CONFIDENT_API_KEY`,
  and optionally `GOOGLE_MODEL`; run the file from the repository root.
- [AgentCore with direct Bedrock calls](agentcore/bedrock.py):
  `pip install -e './python[agentcore]' bedrock-agentcore boto3`; set `BEDROCK_MODEL`,
  `CONFIDENT_API_KEY`, and normal AWS credentials/region; run the file.
- [AgentCore with native Strands](agentcore/strands.py):
  additionally install `strands-agents[otel]`; the same environment applies.

AgentCore examples serve port 8080. POST JSON `{"prompt":"Hello"}` to `/invocations`.
Supply `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` to associate a session and
`traceparent` to continue a distributed trace. These examples do not deploy AWS
resources. Native content policies and backend interpretation are described in
[the integration guide](../docs/integrations.md).

- [CrewAI crew](crewai_agent.py): in-house execution hierarchy plus provider model spans.
