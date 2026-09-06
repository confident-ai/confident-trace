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
