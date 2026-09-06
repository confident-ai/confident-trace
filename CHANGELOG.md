# Changelog

## Unreleased

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
