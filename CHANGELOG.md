# Changelog

## 0.1.0

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
