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

### TypeScript automatic startup

`node --import confident-trace/register <existing-entry>` plus synchronous `init()`
activates all eight existing integrations. Automatic attachments reuse the manual
adapters and their version/method boundaries. `instrumentations: []` selects manual
setup. The preload handles ESM, CommonJS, later imports, and worker preload
inheritance; every worker initializes its own runtime. Bundled SDK code is excluded.

The automatic acceptance suite executes each public entry example against fake
model responses and a local OTLP collector. It verifies model-span counts, privacy,
ESM/CommonJS, late imports, runtime isolation, lifecycle, and tsx startup. SDK
updates must pass both manual adapter tests and automatic startup tests.

### LiteLLM application-side instrumentation

- Python native: LiteLLM >=1.81,<2; tested 1.81.0 on Python 3.10 and 1.100.1
  on Python 3.13. LiteLLM 1.100.1 cannot import on Python 3.10 (upstream
  `typing.NotRequired` import), despite its package metadata allowing installation.
- Native surfaces: `completion`, `acompletion`, and Router equivalents;
  sync/async streaming, tool responses, errors and Confident duplicate suppression.
- Python/TypeScript proxy: existing OpenAI surfaces, identified by explicit exact
  base URL configuration. Verified with real OpenAI SDKs and mock/local transports.
- Integration label describes the SDK (`LiteLLM` or `OpenAI`); proxy spans add
  `confident.gateway.name=litellm`. Provider name identifies LiteLLM as intermediary.
- No live gateway deployment, gateway-internal telemetry, automatic cross-process
  propagation, or other native LiteLLM endpoints are claimed.

### OpenRouter application-side instrumentation

- Python `openrouter>=1.1.136,<1.2`: tested 1.1.136; native `Chat.send` and
  `Chat.send_async`, regular/streaming responses, errors and privacy controls.
- TypeScript `@openrouter/sdk>=1.2.116,<1.3`: tested 1.2.116; native `Chat.send`
  with `chatRequest`, automatic startup and manual `confident-trace/openrouter`.
  CamelCase token and tool-call fields are captured without changing SDK results.
- Existing OpenAI surfaces detect the exact public OpenRouter base URL or explicit
  custom base URLs. Native integration is `OpenRouter`; OpenAI proxy spans retain
  `OpenAI` and add `confident.gateway.name=openrouter`. Provider name is `openrouter`.
- Real SDK tests use mock transports; no live gateway deployment is claimed.
  Native Responses, embeddings, Agent SDK, functional SDK helpers and gateway
  internal telemetry are not covered.

### Portkey application-side instrumentation

- Python `portkey-ai>=2.3.4,<2.4`: tested 2.3.4 on Python 3.10/3.13;
  `Completions.create` and `Responses.create`, sync/async, including streaming.
- TypeScript `portkey-ai>=3.1.0,<3.2`: tested 3.1.0; chat/Responses `create`,
  automatic preload and manual `confident-trace/portkey` setup.
- Real SDK tests cover messages, tokens, tool calls, errors, privacy, early stream
  exit, restoration and native/proxy identity. Python also covers `with_options()`.
- Existing OpenAI surfaces detect the public endpoint and configured custom base
  URLs. Native spans use `Portkey`; OpenAI calls retain their SDK label and add
  `confident.gateway.name=portkey`. Provider name is `portkey`.
- No live gateway validation, gateway-internal spans, prompt APIs, embeddings,
  media endpoints or separate SDK stream/parse helpers are claimed.
- TypeScript Portkey 3.1.0 depends on OpenAI 6.8.1 internally. That copy is skipped
  by the OpenAI auto-instrumentor; the native Portkey boundary captures its calls.
  When supported OpenAI 7 is also present, its enabled status is retained and
  the unsupported copy still produces a version-specific warning.

### Bifrost application-side instrumentation

- Reuses the supported OpenAI and Anthropic SDK versions above; no native gateway
  package or new instrumentation selector is required.
- Explicit `bifrost_proxy_urls` / `bifrostProxyUrls` identify exact base URLs.
  OpenAI chat/Responses and Anthropic Messages preserve protocol extraction and
  SDK integration labels while recording gateway/provider `bifrost`.
- Python real-SDK mock transport tests cover sync/async OpenAI calls and Anthropic
  stream helpers, privacy, header preservation and exact endpoint matching.
  TypeScript tests cover manual adapters, stream helpers and CommonJS preload.
- No live Bifrost deployment, native Go SDK, GenAI/Bedrock gateway detection,
  gateway-internal telemetry or background polling lifecycle is claimed.
- Endpoint setup follows the official [OpenAI integration](https://docs.getbifrost.ai/integrations/openai-sdk/overview)
  and [Anthropic integration](https://docs.getbifrost.ai/integrations/anthropic-sdk/overview).

### TrueFoundry application-side instrumentation

- Reuses the supported OpenAI and Anthropic SDK versions above. Configure exact
  gateway base URLs with `truefoundry_proxy_urls` / `truefoundryProxyUrls`; no new
  SDK dependency or instrumentation selector is required.
- OpenAI chat/Responses and Anthropic Messages retain their SDK integration labels
  and record `confident.gateway.name=truefoundry` and provider `truefoundry`.
- Real-SDK mock transport tests cover Python sync/async calls, Anthropic streams,
  privacy, authentication preservation, API errors and exact endpoint matching.
  TypeScript covers manual adapters/restoration, Messages stream helpers, errors,
  privacy and ESM/CommonJS automatic startup with manual coexistence.
- No live TrueFoundry deployment, Google GenAI/Bedrock gateway detection or
  gateway-internal telemetry is claimed. Authentication follows the official
  [native SDK documentation](https://www.truefoundry.com/docs/ai-gateway/native-sdk-support).
