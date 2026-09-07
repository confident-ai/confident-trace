# TypeScript compatibility

Python support: [compatibility matrix](../python/docs/compatibility.md).

## 0.1.0-alpha.1 — TypeScript alpha

| Component | Supported/tested range |
|---|---|
| Runtime | Node.js 22 and 24; Node >=22 package engine |
| Module loading | ESM and CommonJS sharing one runtime |
| OTel API | >=1.9.0,<2 (independent peer dependency) |
| OTel stable SDK family | >=2.0.0,<3; minimum 2.0.0, current lockfile 2.11.0 |
| OTel OTLP exporters | >=0.200.0,<0.223.0; minimum 0.200.0, current lockfile 0.222.0 |
| Transport | HTTP/protobuf; gRPC with configured collector |
| Semantic contract | Selected GenAI 1.37.0 constants and unchanged shared wire fixtures |
| Provider integrations | OpenAI Chat Completions/Responses create; Anthropic Messages create/stream; Google models generateContent/generateContentStream |
| OpenAI JavaScript SDK | >=7.10.0,<8; tested 7.10.0 |
| Anthropic JavaScript SDK | >=0.124.0,<0.125; tested 0.124.0 |
| Google GenAI JavaScript SDK | >=2.21.0,<3; tested 2.21.0 |
| Vercel AI SDK | >=7.0.93,<8; tested 7.0.93 with @ai-sdk/otel 1.0.93 (>=1.0.93,<2) |
| Mastra | @mastra/core >=1.64.0,<2; @mastra/observability >=1.17.5,<2; tested 1.64.0 / 1.17.5 |
| LangChain | @langchain/core >=1.2.9,<2; langchain >=1.5.10,<2; tested 1.2.9 / 1.5.10 |
| LangGraph | >=1.4.14,<2; tested 1.4.14 with core 1.2.9 |
| OpenAI Agents SDK | >=0.17.0,<0.18; tested 0.17.0 |
| Custom span APIs | Not implemented; use native OTel spans |
| Type declarations | TypeScript 5.9; NodeNext and bundler consumer checks |

CI tests Node 22/24 against minimum and current supported OTel dependencies, plus
packed-artifact consumers. No backend deployment or live credentials are involved.
Provider tests use the real JavaScript SDKs against local HTTP/SSE responses.
Optional peers are loaded only by the application. TypeScript preserves native
SDK method signatures, promise helpers, and stream controls; raw `asResponse()`
ends the span without parsing the body. Streams must be consumed or closed.
Separate OpenAI streaming helpers, Google chat helpers, beta APIs, stream teeing,
and full multimodal normalization are not claimed. See the TypeScript README
for lifecycle and content-capture boundaries. Python's provider version ranges
do not imply supported JavaScript SDK versions.

Framework integration tests exercise the real AI SDK telemetry hooks (generation,
streaming, tools, aborts, errors and concurrency) and Mastra's real observability
pipeline (agent/model/tool trees, event spans, sampling, and lifecycle). Mastra
resumed-span snapshots test export without a prior start event. Both entry points
are checked in packed ESM/CommonJS and TypeScript NodeNext/Bundler consumers without
installing the optional framework packages. No live model credentials are used.

AI SDK 5/6 telemetry, Mastra execution-context bridging, non-trace Mastra signals,
and embedding/reranking/media AI SDK APIs are outside the tested scope. Frameworks
own their internal content buffers; the adapters bound exported attributes.

LangChain/LangGraph tests use real Runnable chains, chat models with local fake
responses, tools, graph streams, concurrent runs, createAgent, and checkpoint
interrupt/resume. OpenAI Agents SDK tests use its native trace/span lifecycle and
Runner with a local model. These callback adapters preserve observed hierarchy,
not native IDs or ambient execution context. They require root/start callbacks;
cross-process trace reconstruction and server-side Realtime traces are excluded.
All three subpaths are checked in packed ESM/CommonJS and declaration consumers
without optional SDK packages. See the TypeScript README for shutdown, content,
and capacity controls.
