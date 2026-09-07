<img src="assets/confident-trace-banner-radial-4s.svg" alt="Confident Trace — illuminated paths across a star-filled landscape" width="100%" />

# `confident-trace`: Otel Native Tracing for AI Systems

Confident Trace brings model calls, agent runs, tools, retrieval, and application
code into OpenTelemetry traces. Python and TypeScript SDKs work with your existing
provider clients and export through standard OTLP to Confident or an
OpenTelemetry Collector.

## SDKs

| SDK | Runtime | Integration model |
| --- | --- | --- |
| Python | Python 3.10+ | Automatic instrumentation for supported installed SDKs, framework integrations, and custom span decorators. |
| TypeScript / JavaScript | Node.js 22+; tested on 22 and 24 | Explicit client instrumentation, framework adapters, and custom function wrappers. ESM and CommonJS supported. |

Each SDK is independently installable. Provider and framework dependencies are
optional; use the integrations your application needs.

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

## Agent frameworks

“Native OTel” means the integration uses the framework's own OpenTelemetry spans.
Other integrations capture framework execution or adapt its callbacks and tracing
hooks. A dash indicates no dedicated integration in that SDK.

| Framework | Python | TypeScript / JavaScript |
| --- | --- | --- |
| LangChain | Chains, models, tools, and retrievers, with execution context and callback hierarchy. | Callback adapter for chains, models, tools, and retrievers. |
| LangGraph | Local graph execution, nodes, subgraphs, streaming, and checkpoint resume. | Callback adapter for graph runs, nodes, streaming, and checkpoint resume. |
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
  to Confident-managed content; native framework and third-party instrumentation
  retain their own content policies.

Confident-owned GenAI spans use selected OpenTelemetry GenAI 1.37.0 conventions.
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
