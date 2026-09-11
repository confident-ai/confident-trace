# Integrations

Prefer Confident Trace's supported integration over custom spans around the same
provider or framework call.

## Required Documentation Rule

1. Identify the language, model provider, agent framework, LLM gateway,
   bundler, and existing OpenTelemetry setup.
2. Read the exact documentation linked below before writing setup code. The
   docs—not this skill—are authoritative for supported versions, installation
   extras, instrumented methods, setup, limitations, and lifecycle.
3. Use automatic instrumentation unless the docs require a native callback,
   processor, exporter, or manual adapter.
4. Add custom spans only around application-owned boundaries or unsupported
   components.
5. Do not stack Confident Trace with another instrumentor on the same SDK call.

Do not reproduce a remembered integration API or infer support from the package
being installed. Confirm it in the current docs first.

## Documentation Index

### Shared coverage

- [SDK and integration coverage](https://github.com/confident-ai/confident-trace/blob/main/README.md)
- [Python setup and integration guide](https://github.com/confident-ai/confident-trace/blob/main/python/README.md)
- [TypeScript setup and integration guide](https://github.com/confident-ai/confident-trace/blob/main/typescript/README.md)
- [Python framework ownership and coverage](https://github.com/confident-ai/confident-trace/blob/main/python/docs/frameworks.md)
- [Python native integration setup and boundaries](https://github.com/confident-ai/confident-trace/blob/main/python/docs/integrations.md)
- [Python tested compatibility](https://github.com/confident-ai/confident-trace/blob/main/python/docs/compatibility.md)

### Python

- Providers—OpenAI, Anthropic, Google GenAI, and Bedrock:
  [Python supported surfaces](https://github.com/confident-ai/confident-trace/blob/main/python/README.md#what-is-supported)
- LangChain and LangGraph:
  [Python LangChain and LangGraph setup](https://github.com/confident-ai/confident-trace/blob/main/python/README.md#langchain-and-langgraph)
- Google ADK, AgentCore, Microsoft Agent Framework, Pydantic AI, Strands,
  OpenAI Agents, and Claude Agent SDK:
  [native integration setup and limitations](https://github.com/confident-ai/confident-trace/blob/main/python/docs/integrations.md)
- CrewAI, LlamaIndex, Agno, and smolagents:
  [framework ownership](https://github.com/confident-ai/confident-trace/blob/main/python/docs/frameworks.md)
- LiteLLM, OpenRouter, Portkey, Bifrost, and TrueFoundry:
  [Python gateway setup](https://github.com/confident-ai/confident-trace/blob/main/python/README.md#litellm)

### TypeScript and JavaScript

- Automatic setup and supported integrations:
  [TypeScript automatic setup](https://github.com/confident-ai/confident-trace/blob/main/typescript/README.md#automatic-setup)
- OpenAI, Anthropic, and Google GenAI bundled/manual setup:
  [manual provider integrations](https://github.com/confident-ai/confident-trace/blob/main/typescript/README.md#manual-provider-integrations)
- Vercel AI SDK:
  [manual Vercel AI SDK integration](https://github.com/confident-ai/confident-trace/blob/main/typescript/README.md#manual-vercel-ai-sdk-integration)
- Mastra:
  [manual Mastra integration](https://github.com/confident-ai/confident-trace/blob/main/typescript/README.md#manual-mastra-integration)
- LangChain and LangGraph:
  [manual LangChain and LangGraph integration](https://github.com/confident-ai/confident-trace/blob/main/typescript/README.md#manual-langchain-and-langgraph-integration)
- OpenAI Agents:
  [manual OpenAI Agents integration](https://github.com/confident-ai/confident-trace/blob/main/typescript/README.md#manual-openai-agents-integration)
- LiteLLM, OpenRouter, Portkey, Bifrost, and TrueFoundry:
  [TypeScript gateway setup](https://github.com/confident-ai/confident-trace/blob/main/typescript/README.md#litellm-proxy)

If the application already owns a `TracerProvider`, keep that provider:

- Python: pass it to `init(tracer_provider=provider, ...)`.
- TypeScript: install `createSpanProcessor()` from `confident-trace/otel` while
  constructing the provider; `init()` cannot attach to an existing provider.

Use the `confident-otel` skill only when exporting raw OpenTelemetry without
the confident-trace package.

## Python

After confirming the current docs, Python integrations are auto-detected after
`init()`:

- Providers: OpenAI, Anthropic, Google GenAI, and AWS Bedrock Runtime.
- Frameworks: LangChain, LangGraph, OpenAI Agents, CrewAI, LlamaIndex, Agno,
  smolagents, Google ADK, Microsoft Agent Framework, Pydantic AI, Strands,
  AgentCore, and Claude Agent SDK.
- Gateways: LiteLLM, OpenRouter, and Portkey natively; Bifrost and TrueFoundry
  through OpenAI or Anthropic clients with explicitly configured proxy URLs.

OpenAI Agents requires `pip install "confident-trace[openai-agents]"`.
AgentCore requires `pip install "confident-trace[agentcore]"`.

For custom gateway URLs, pass the matching `*_proxy_urls` option to `init()`.

## TypeScript and JavaScript

After confirming the current docs, automatic instrumentation supports:

- Providers: OpenAI, Anthropic, and Google GenAI.
- Frameworks: Vercel AI SDK, LangChain, LangGraph, Mastra, and OpenAI Agents.
- Gateways: OpenRouter and Portkey natively; LiteLLM, Bifrost, and TrueFoundry
  through supported provider clients and configured proxy URLs.

Automatic mode requires the `confident-trace/register` Node preload and `init()`.
Bundled applications use the package's manual subpath adapters instead.

For custom gateway URLs, pass `litellmProxyUrls`, `openrouterProxyUrls`,
`portkeyProxyUrls`, `bifrostProxyUrls`, or `truefoundryProxyUrls`.

## Existing OpenTelemetry

The SDK supports application-owned OpenTelemetry providers and collector
pipelines. Preserve the application's resources, sampler, propagator, and
unrelated processors. Do not register a second global provider or duplicate an
existing exporter.

If the user explicitly wants a vendor-neutral setup with no confident-trace
package dependency, use the `confident-otel` skill instead.
