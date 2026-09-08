# Changelog

## Unreleased — TypeScript automatic instrumentation

- Add `confident-trace/register` for Node preload instrumentation of all eight
  existing integrations. Keep `init()` in the existing entry file and add
  `--import confident-trace/register` to its startup command.
- Add integration selection and `getInstrumentationStatus()`, with a missing-hook
  warning. Use `init({ instrumentations: [] })` for manual-only tracing.
- Coordinate automatically owned framework adapter lifecycle through the runtime.
  Preserve manual APIs, existing SDK method coverage, and content policies.
- Add executable entry examples and automatic startup/OTLP acceptance suites.


## Unreleased — TypeScript SDK

- Add the TypeScript OpenTelemetry runtime, OTLP exporters, bounded content policy,
  and custom span/trace helpers with ESM/CommonJS declarations and packaging.
- Add OpenAI, Anthropic, Google GenAI, Mastra, Vercel AI SDK, LangChain,
  LangGraph, and OpenAI Agents SDK integrations with span types and content controls.
- Add real SDK tests, wire fixtures, package consumer checks, and Node 22/24 CI.
- Preserve the existing Python implementation and its semantic registry.

Integration spans now use the shared public `Integration` enum and stamp
`confident.span.integration` with canonical Cloud UI labels, including enabled
native framework spans. Claude CLI subprocess exports remain external.

## Unreleased

- Add in-house LlamaIndex, Agno and smolagents execution tracing with provider-owned
  model spans, scoped context, stream lifecycle and isolated real-framework tests.

- Add in-house CrewAI execution tracing with provider-owned model spans, scoped
  tool worker context, lifecycle cleanup, and real-framework coverage.

- Add OpenAI Agents SDK through an optional OpenInference bridge, preserving native
  processors and suppressing overlapping provider spans on the same OTel provider.
- Add Claude Agent SDK subprocess OTLP configuration with copied options, explicit
  override/disable handling, W3C propagation tests and native export boundaries.

- Add native Pydantic AI and Strands integrations with model-span deduplication,
  real-framework offline tests, examples, and native-content documentation.
  Recognize the actual SDK provider for native spans, including Pydantic agents
  configured with explicit providers. Track Strands cancellation/early-close
  leaks as upstream limitations without patching its span lifecycle.

- Add opt-in DEBUG diagnostics for shared fail-open telemetry calls, with package
  operation/error-type labels, bounded output, and no payloads or tracebacks.

- Remove framework-only install extras for ADK and Microsoft Agent Framework.
  Applications supply their framework SDKs; the AgentCore extra now installs only
  OTel ASGI instrumentation. Development/test extras remain available.

- Add Microsoft Agent Framework native OTel enablement and inference deduplication.
- Add an Agent Framework example, real-SDK offline tests, tested dependency
  constraints, CI coverage, and native content/lifecycle documentation.

- Centralize runtime telemetry names by ownership; preserve the generated emission contract and independent native recognition vocabulary. Native-span session enrichment now uses Confident extensions rather than adding or overwriting GenAI conversation attributes.

- Remove native framework version pins and runtime gates; use capability checks, documented tested versions, reproducible test constraints, and unconstrained latest CI.

- Verify native Google ADK 2.8.0 spans and suppress only overlapping Confident inference spans.
- Add AgentCore 1.22.0 HTTP invocation propagation through upstream OTel ASGI middleware, preserving existing server instrumentation.
- Add optional extras, real-framework offline tests, examples, and explicit native-content/hosted-verification boundaries.

## 0.1.0

Organized Python instrumentation into provider-owned integration packages, with
shared lifecycle and bounded stream accumulation. Public APIs and telemetry
remain unchanged. See the Python architecture guide for extension points.

Added AWS Bedrock Runtime Converse/ConverseStream instrumentation for Boto3,
including bounded event streaming, tool content, examples, and SDK compatibility
tests. AgentCore, ADK, and Microsoft agent integrations are documented in the
roadmap as required future work.

Initial Python release: automatic OpenAI, Anthropic, and Google GenAI tracing over
standard OTel, optional `@span` for custom steps/tools, environment configuration,
HTTP/gRPC export, and bounded content capture. No separate turn API.

GenAI semantic conventions remain pinned to **1.37.0**. A shared, attributed
registry now generates Python constants and release compatibility documentation.
The release manifest binds implementation coverage to the exact registry hash.

Compared with the development implementation, message attributes retain valid
array shapes under truncation and include finish reasons. Provider extraction
handles request aliases, tool messages, conversation IDs, service tiers, server
identity, and reported token totals. Streams preserve separate candidates and
continue recording usage after content limits. Async detection unwraps SDK
argument-validation decorators so spans remain open through async execution.

Custom tool spans now follow `execute_tool {name}` naming. Tool argument/result
attributes from newer conventions are deliberately not emitted under the 1.37.0
schema URL. Known gaps and supported dependencies are listed in the generated
compatibility matrix. Backend mapping and metrics/log pipelines are separate work.
