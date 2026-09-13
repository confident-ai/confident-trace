<img src="assets/confident-trace-banner-radial-4s.svg" alt="Confident Trace — illuminated paths across a star-filled landscape" width="100%" />

# `confident-trace`: Otel Native Tracing for AI Systems

Confident Trace brings agent runs, model calls, tools, retrieval, and application
code into OpenTelemetry traces. Python and TypeScript SDKs work with your existing
provider clients and export through standard OTLP to Confident AI or an
OpenTelemetry Collector.

- **25 integrations:** 16 agent frameworks, 4 model providers, and 5 LLM gateways
  across Python and TypeScript, with language-specific coverage listed below.
- **OpenTelemetry semantic conventions:** Confident Trace emits GenAI spans using
  the supported subset of GenAI 1.37.0 conventions for model calls, messages, tools,
  and token usage. Native framework spans retain their original conventions.

## SDKs

| SDK | Runtime | Integration model |
| --- | --- | --- |
| Python | Python 3.10+ | Automatic instrumentation for supported installed SDKs, framework integrations, and custom span decorators. |
| TypeScript / JavaScript | Node.js 22+; tested on 22 and 24 | Automatic instrumentation through `init()` and a Node preload, plus manual adapters and custom function wrappers. ESM and CommonJS supported. |

Each SDK is independently installable. Provider and framework dependencies are
optional; use the integrations your application needs.

For TypeScript, call `init()` in your existing entry file and launch with
`node --import confident-trace/register dist/index.js` (keep your own filename).
See the [TypeScript setup and examples](typescript/README.md#automatic-setup).

## Environment variables

Set `CONFIDENT_API_KEY` to your project's API key. Use `CONFIDENT_OTEL_ENDPOINT`
to set the full traces endpoint for EU or self-hosted deployments. It defaults to
`https://otel.confident-ai.com/v1/traces` for US Confident Cloud.

```sh
export CONFIDENT_API_KEY=your-api-key
# EU Confident Cloud
export CONFIDENT_OTEL_ENDPOINT=https://eu.otel.confident-ai.com/v1/traces
# Self-hosted: use your deployment's full traces endpoint
# export CONFIDENT_OTEL_ENDPOINT=https://otel.your-company.com/v1/traces
```

Explicit endpoint arguments override `CONFIDENT_OTEL_ENDPOINT`. Standard OTel
endpoint variables do not affect Confident Trace.

## Agent frameworks

“Native OTel” means the integration uses the framework's own OpenTelemetry spans.
Other integrations capture framework execution or adapt its callbacks and tracing
hooks. A dash indicates no dedicated integration in that SDK.

| Framework | Python | TypeScript / JavaScript |
| --- | --- | --- |
| LangChain | Chains, models, tools, and retrievers, with execution context and callback hierarchy. | Callback adapter for chains, models, tools, and retrievers. |
| LangGraph | Local graph execution, nodes, subgraphs, streaming, and checkpoint resume. | Callback adapter for graph runs, nodes, streaming, and checkpoint resume. |
| Deep Agents | Python 3.11+: agent graphs, nested subagents, tools, streaming, and interrupt/resume through the LangGraph bridge. | — |
| OpenAI Agents SDK | Agents, models, tools, handoffs, guardrails, and streams through the optional OpenInference bridge. | Trace processor for native agent and workflow span callbacks. |
| CrewAI | Crews, tasks, agents, tools, and Flows. | — |
| LlamaIndex | Agents, tools, retrieval, and workflows. | — |
| Agno | Agents, teams, tools, and workflow steps. | — |
| smolagents | Agent runs, planning, steps, and local tool execution. | — |
| Google ADK | Native OTel agent, model, tool, and streaming spans. | — |
| Microsoft Agent Framework | Native OTel agent, tool, and workflow spans. | — |
| Pydantic AI | Native OTel agent runs, tools, and streams. | — |
| Strands | Native OTel agent, model, and tool spans. | — |
| Amazon Bedrock AgentCore | Local HTTP `/invocations` tracing through ASGI middleware. | — |
| Claude Agent SDK | Subprocess OTLP configuration and W3C context propagation; native CLI trace output remains unverified. | — |
| Vercel AI SDK | — | AI SDK 7 telemetry adapter for generation, streaming, steps, models, and tools. |
| Mastra | — | Export adapter for agent, model, tool, and workflow spans. |

Framework coverage follows each framework's available hooks. Python CrewAI,
LlamaIndex, Agno, and smolagents integrations trace execution structure; supported
provider integrations supply model-call spans. TypeScript callback adapters
preserve observed hierarchy but do not activate those spans around application
code. Mastra exports completed spans without bridging execution context.

Strands has known upstream span-closure limitations on cancellation and early
stream exit. AgentCore coverage is local application telemetry; hosted AWS
telemetry and WebSocket/A2A endpoints are outside the supported scope.

## Model providers

| Provider | Python | TypeScript / JavaScript | Covered APIs |
| --- | --- | --- | --- |
| OpenAI | Supported | Supported | Chat Completions and Responses `create`, including streaming. |
| Anthropic | Supported | Supported | Messages `create` and `stream`. |
| Google GenAI | Supported | Supported | Content generation and streaming content generation. |
| AWS Bedrock Runtime | Supported via Boto3 | — | `converse` and `converse_stream`. |

Python provider integrations support synchronous and asynchronous calls, except
Boto3, which uses synchronous calls and event streams. TypeScript integrations
preserve the supplied client's method signatures and stream controls.

Provider spans capture model information, timing, status, available token usage,
messages, and supported tool-call data. Coverage applies to the APIs listed above;
embeddings, realtime, provider batch APIs, image/audio generation, and OpenAI's
separate `responses.stream` helper are outside this scope. Multimodal binary
payloads are omitted.

## LLM Gateways

Gateway support traces calls from your application through native gateway SDKs or
supported provider clients configured to use a gateway. Each gateway counts once
in the integration total, regardless of the client or language used.

| Gateway | Python | TypeScript / JavaScript |
| --- | --- | --- |
| LiteLLM | Native `completion` / `acompletion` and Router calls; OpenAI client proxy detection. | OpenAI client proxy detection. |
| OpenRouter | Native `chat.send` / `send_async`; OpenAI client proxy detection. | Native `chat.send`; OpenAI client proxy detection. |
| Portkey | Native chat completions and Responses `create`; OpenAI client proxy detection. | Native chat completions and Responses `create`; OpenAI client proxy detection. |
| Bifrost | OpenAI and Anthropic client proxy detection. | OpenAI and Anthropic client proxy detection. |
| TrueFoundry | OpenAI and Anthropic client proxy detection. | OpenAI and Anthropic client proxy detection. |

Proxy coverage uses the OpenAI Chat Completions/Responses and Anthropic Messages
APIs listed above, including streaming. Python native gateway integrations also
support synchronous and asynchronous calls. TypeScript automatic instrumentation
uses `init()` plus the register preload; OpenRouter and Portkey also have manual
adapters.

OpenAI clients using the public OpenRouter or Portkey endpoint are recognized
automatically. Register other full client base URLs with `litellm_proxy_urls`,
`openrouter_proxy_urls`, `portkey_proxy_urls`, `bifrost_proxy_urls`, or
`truefoundry_proxy_urls` in Python, or the corresponding `*ProxyUrls` options in
TypeScript. Matching uses the exact origin and base path, ignoring trailing
slashes.

Proxy spans retain their provider SDK integration label and add
`confident.gateway.name`; `gen_ai.provider.name` identifies the gateway. Native
SDK spans use their gateway integration label. This covers application calls;
gateway-internal routing, retries, and fallbacks are outside this scope. Bifrost
and TrueFoundry gateway detection through Google GenAI or Bedrock clients is not
included. Gateway tests use real SDKs with mock transports, without live gateway
validation.

## Custom tracing and OpenTelemetry

- **Application spans:** Python decorators and span scopes; TypeScript function
  wrappers and scoped callbacks. Both support synchronous work, asynchronous
  work, and sync/async generators.
- **Trace context:** add input, output, metadata, tags, user IDs, and conversation
  IDs to application traces. TypeScript also provides explicit conversation-turn
  scopes and span-level update helpers.
- **Existing instrumentation:** export spans through a shared OpenTelemetry
  provider while retaining native attributes, events, and parent relationships.
- **Export and lifecycle:** OTLP over HTTP/protobuf or gRPC, standard OTel
  configuration, batch processing, explicit flush, and shutdown. gRPC requires a
  configured collector or compatible endpoint.
- **Content controls:** capture opt-out, redaction, and bounded content attributes
  with a default 16 KiB limit. Capture is enabled by default. These controls apply
  to content managed by Confident Trace; native framework and third-party instrumentation
  retain their own content policies.

The SDKs export traces; they do not provide a metrics or logs pipeline.

## Repository

| Directory | Contents |
| --- | --- |
| `python/` | Python SDK, integrations, tests, examples, and benchmarks. |
| `typescript/` | TypeScript SDK, integrations, tests, and examples. |
| `spec/` | Versioned GenAI registry, release manifests, semantic contracts, and shared fixtures. |
| `tools/` | GenAI registry generation and validation. |
| `scripts/` | Shared artifact synchronization and CI tooling. |

## License

[Apache License 2.0](LICENSE).
