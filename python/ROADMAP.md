# Python integration roadmap

Bedrock inference, native Google ADK interoperability, and AgentCore application
request-boundary integration are locally tested with ADK 2.8.0 and AgentCore 1.22.0. Hosted AWS/ADOT and
backend mapping verification remain outstanding; see docs/integrations.md.
The remaining integrations below have no new priority relative to provider work.

## Framework integrations implemented locally

- LlamaIndex, Agno, and smolagents: in-house structural tracing with provider-owned
  inference spans. See [setup, tested scope and limitations](docs/frameworks.md).
  Overlapping third-party framework instrumentation or unreconciled gateway model
  exports require choosing one owner; propagation alone does not deduplicate.

- CrewAI: in-house execution hooks for crews, tasks, agents, tools, and flows;
  existing provider adapters own model spans without a second CrewAI inference span.
  See [coverage and boundaries](docs/crewai.md).

- LangChain / LangGraph: in-house GenAI callback bridge, node/tool execution context,
  provider deduplication, concurrency/stream/cancellation/checkpoint tests with
  LangChain 1.4.0, Core 1.6.2 and LangGraph 1.2.11.

- OpenAI Agents SDK: optional OpenInference bridge, native model deduplication,
  offline agent/tool/handoff/guardrail/stream tests with SDK 0.22.0 and bridge 2.2.1.
- Claude Agent SDK: **experimental native tracing**. SDK 0.2.152 / CLI 2.1.259
  passes fake-model parentage tests, but live runs have missing or disconnected
  native spans. Collector acceptance is observed; connected trace reliability is
  unresolved. For strict trace ownership, disable child telemetry and retain only
  the Python invocation span. Track upstream readiness/context behavior and
  require a production-startup regression before removing this limitation.
  See [evidence and upstream issue draft](docs/claude-native-tracing.md).

- Microsoft Agent Framework: native OTel enablement, model deduplication, and
  real-framework agent/tool/workflow tests.
- Pydantic AI: native enablement, existing settings preservation, provider ownership,
  agent/tool/stream tests and model deduplication; tested with 2.40.0.
- Strands: existing native OTel spans and model deduplication; tested with 1.54.0.
  Native cancellation and early stream closure leak spans upstream; tracked in tests.

The Python SDK integrates application frameworks such as Google ADK and Microsoft
Agent Framework. Hosted platforms such as Microsoft Foundry and Google Gemini
Enterprise agents are outside this SDK integration roadmap. Their telemetry
export setup and receiver mapping/validation belong to the platform/backend
integration work.

For each integration, support requires a documented setup, supported dependency
and convention versions, runnable examples, lifecycle tests, correct parentage
and conversation association, and no duplicate model spans. Prefer native OTel;
do not rewrite third-party spans into our pinned convention version.

`init()` should configure everything available in the application process.
Cloud-service internal telemetry may need separate platform export configuration;
it cannot be exposed by an SDK running only in the caller. Backend interpretation
and ClickHouse mapping remain separate work.

References:
- [AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-get-started.html)
- [Google ADK tracing](https://adk.dev/observability/traces/)
- [Microsoft Agent Framework observability](https://github.com/microsoft/agent-framework/blob/main/python/samples/02-agents/observability/README.md)

## Model providers and other candidates

- Bedrock Runtime: Boto3 `converse` and `converse_stream` are implemented. `invoke_model`, `invoke_model_with_response_stream`, native async
  clients are outside that support claim. AgentCore application integration is separate.
- Other candidates remain Azure OpenAI/Vertex AI deployment validation and
  individually tested OpenAI-compatible endpoints.
- Existing provider gaps include embeddings and OpenAI's `responses.stream()` helper.

See the [release matrix](docs/compatibility.md) for implemented coverage.


## Remaining framework candidates

- AutoGen

Haystack is excluded from the current framework list.

## AI gateways: export, propagation, and client coverage

Research checked **2026-09-07** against vendor documentation. These are researched
integration candidates, not tested compatibility claims. Gateway deployment may
be SaaS, customer-hosted, or embedded; "gateway-side" is more accurate than
"cloud-only". This classification is our recommendation based on the sources.

Three independent capabilities need separate work:

1. **Client instrumentation:** our Python SDK records application, framework, and
   supported provider calls. This does not expose remote gateway internals.
2. **Context propagation:** request headers or vendor-specific fields carry trace
   and parent IDs across the network. They do not deliver spans to our backend.
3. **Gateway export:** the gateway sends its own spans to our OTLP receiver or an
   intermediate collector. With compatible propagation, client and gateway spans
   can form one distributed trace. Both export paths must be configured.

W3C context propagation carries execution identity, independently of telemetry
export. See [OpenTelemetry context propagation](https://opentelemetry.io/docs/concepts/context-propagation/).

### Product classification

| Product / usage | Gateway or native export to our backend | Parent propagation evidence | Roadmap placement |
|---|---|---|---|
| **TrueFoundry AI Gateway** | Documented external trace export with configurable endpoint/auth, HTTP protobuf or JSON, or gRPC. Configured in gateway settings; TrueFoundry-managed storage can remain enabled. | The reviewed gateway export and request-header docs do not establish inbound W3C continuation. Mark it **unverified**, not unsupported. | Primarily gateway export/backend compatibility. Confirm propagation separately; no dedicated Python adapter initially. [Export docs](https://www.truefoundry.com/docs/ai-gateway/export-opentelemetry-data), [request headers](https://www.truefoundry.com/docs/ai-gateway/request-headers). |
| **Portkey** | Experimental gateway export to an external OTLP traces endpoint is documented for **Enterprise/Self-Hosted**. Its general OTel ingestion endpoint instead sends data **into Portkey**, which is the opposite direction. | Accepts `traceparent` and `baggage`. Portkey-specific headers take precedence. Docs map W3C parent ID to `x-portkey-span-id`; verify exported span IDs/parents before assuming conventional remote-child structure. | Gateway export plus propagation validation, subject to deployment availability. Its LangChain callback support is a separate client-side concern. [OTel export](https://portkey.ai/docs/product/observability/opentelemetry), [tracing/headers](https://portkey.ai/docs/product/observability/traces). |
| **LiteLLM Proxy** | Native OTLP export. Current docs describe opt-in OTel v2 (`LITELLM_OTEL_V2=true`) with HTTP/gRPC destinations and canonical `gen_ai.*` attributes; vendor presets can add other vocabularies. | Documents inbound `traceparent` continuation under the application's trace. | Gateway export/backend compatibility plus W3C propagation; select and test one tracing generation/configuration. [Proxy OTel v2](https://docs.litellm.ai/docs/observability/opentelemetry_v2). |
| **LiteLLM Python SDK, without proxy** | In-process native OTel callback support (`litellm.callbacks = ["otel"]`). This is distinct from proxy OTel v2. | Existing OTel context and explicit parent metadata are documented; provider ownership/export setup still needs testing with our Runtime. | A genuine **Python SDK integration candidate**. Investigate native coexistence, preserving callbacks/provider ownership, and inference deduplication before writing wrappers. [SDK/OTel v1](https://docs.litellm.ai/docs/observability/opentelemetry_integration). |
| **Bifrost (Maxim AI gateway)** | OTel plugin exports to configurable HTTP/gRPC collectors. It is also usable with Bifrost's **Go SDK**, distinct from a Python framework adapter. | Documents inbound W3C `traceparent` taking precedence over optional session grouping. | Primarily gateway export plus W3C validation. Prefer `trace_type=genai_extension`; alternatives include `open_inference` and `vercel`. Keep `group_traces_by_session=false` to preserve independent invocation traces. [OTel plugin/configuration](https://docs.getbifrost.ai/features/observability/otel). |
| **OpenRouter** | Hosted **Broadcast → OpenTelemetry Collector** sends OTLP **HTTP/JSON** to a configurable traces endpoint with custom auth headers. | Documents request-body `trace.trace_id` and `trace.parent_span_id` for linking external traces. Inbound W3C headers were not established by the reviewed docs; body linkage is not proof of W3C support. | Hosted export/backend compatibility, plus a small request-context adapter if needed. Verify JSON ingestion and exact preservation of OTel IDs. [OTLP destination](https://openrouter.ai/docs/guides/features/broadcast/otel-collector), [external-trace linkage](https://openrouter.ai/docs/guides/features/broadcast/overview). |

TrueFoundry also documents an application tracing SDK and OTel collector backend;
that is a separate application-instrumentation/ingestion product, not evidence that
our SDK must wrap its gateway. [Application tracing overview](https://www.truefoundry.com/docs/tracing/overview).

### LangChain calling a gateway

For a normal LangChain model call routed through Portkey (or another gateway),
our LangChain bridge records the **local model operation** from callbacks. An
OpenAI-compatible client route can similarly use the existing OpenAI wrapper,
subject to endpoint-specific verification. This does not automatically capture
routing, cache decisions, guardrails, retries, or actual provider attempts inside
the gateway. Nor does the existence of a LangChain model object prove that W3C
headers were injected or that gateway spans reached our backend.

The current Confident LangChain/OpenAI adapters do **not** automatically inject
outbound W3C headers. Future gateway work needs request-scoped propagation or
compatible HTTP instrumentation. Never store a current trace ID in shared client
configuration: concurrent requests must each receive their own current context.
OpenRouter's documented body fields require a distinct mapping if W3C support
cannot be confirmed.

Target flow:

```text
application / agent / LangChain spans ───────────────→ our OTLP backend
                    │
                    └─ model request + current parent context → gateway
                                                               │
                                      gateway-generated spans ─┘→ our OTLP backend
```

The gateway does not send its exported spans back through the Python SDK.
The flow above describes possible transport paths, **not approval to enable both
sources for the same model operation**. W3C parentage alone does not deduplicate
SDK and gateway records.

### Primary requirement: no duplicate model records

**Duplicate-free ingestion is a release blocker for every gateway integration.**
For example, exporting Portkey to our backend while also recording its model call
through Confident's SDK must result in one canonical logical model operation, not
two model entries or two evaluation inputs. Merely subtracting duplicated token
costs, or displaying duplicate inference spans as parent and child, is insufficient.

Current status: this repository has **no implemented or verified general
cross-process SDK/gateway reconciliation** for these five products. Existing
in-process provider-wrapper suppression does not solve independent gateway export.
None of the researched export/propagation capabilities establishes that combined
SDK + gateway capture will be duplicate-free in our backend. Do not advertise it
as supported until the following requirements pass:

- Establish a stable per-operation correlation contract connecting the SDK call
  to the gateway record(s). Trace ID alone is insufficient: one trace can contain
  many model calls. Matching prompt/model/time is also insufficient. W3C ancestry
  can help locate a boundary but does not, by itself, identify equivalent records.
  Distinct exported span IDs will not collapse through ordinary OTLP deduplication.
- Choose an authoritative model record and reconcile the overlapping record
  deterministically. UI, evaluations, token/cost totals and query results must see
  the same canonical operation. If a redundant span is removed, preserve/reparent
  its children or retain it strictly as a non-model transport envelope; it must
  not remain a second inference or evaluation input.
- Preserve real gateway retries/fallback/provider attempts as distinct attempts
  under their logical operation. Do not collapse genuine repeated calls, parallel
  identical prompts, or cached requests using broad trace-level heuristics.
- Handle either arrival order, delayed spans, exporter retries and repeated
  deliveries idempotently. Sampling or missing exports must not leave a gap caused
  by speculative local suppression, or produce two evaluations when a late record
  arrives. Define completion/finalization behavior before enabling dual capture.

**Until reconciliation is proven, choose one authoritative tracing method for
model operations:**

| Mode | Recommendation | Trade-off |
|---|---|---|
| **SDK-authoritative** | Keep Confident's framework/provider model tracing; disable that gateway's model export to our backend. This is the default safe recommendation today. | Application/model/tool hierarchy remains available; gateway-internal routing, cache and retry details are unavailable through this path. |
| **Gateway-authoritative** | Export gateway model spans; disable overlapping local model instrumentation. Retain local request/agent/tool tracing only where supported configuration can exclude its model records. | Gateway detail is available; local coverage may be reduced and context propagation must be configured separately. |

Turning off the OpenAI adapter alone does **not** turn off model spans emitted by
our LangChain bridge or another framework's native instrumentation. If selective
suppression/filtering is unavailable, disable the overlapping framework integration
as well (manual request spans may remain), or choose SDK-authoritative mode.
Do not claim an unsupported selective-disable setting exists. If reliable
correlation/reconciliation cannot be achieved, **give up one of the overlapping
model tracing paths** rather than shipping duplication. Document that limitation
explicitly for the affected product/deployment/version.

### Follow-up work and acceptance criteria

- **Backend/gateway track:** TrueFoundry, Portkey, LiteLLM Proxy, Bifrost, OpenRouter.
  Add setup recipes and captured-payload fixtures before advertising support.
  Verify endpoint paths, auth, HTTP JSON/protobuf versus gRPC, and export feature
  availability for the actual hosted/self-hosted deployment.
- **Python SDK track:** investigate LiteLLM's in-process callback integration;
  validate existing OpenAI-compatible and LangChain client paths for all five.
  Add provider-specific adapters only for demonstrated uncovered client behavior.
- **Propagation track:** verify W3C entry behavior for LiteLLM Proxy and Bifrost;
  verify Portkey's ID mapping and precedence; establish TrueFoundry's actual
  contract; test OpenRouter's documented body linkage. Inbound gateway context
  support does not prove downstream forwarding to every model provider.
- **Semantics:** inspect emitted scopes, versions, content shapes, statuses, usage,
  cache/fallback attempts and extensions. Portkey documents GenAI 1.40.0; other
  modes/presets may differ from our 1.37.0 emission contract. Preserve foreign
  spans and map them in the backend rather than relabeling their schema.
- **End-to-end acceptance:** `ct.span("request")` → LangChain/model → gateway →
  provider attempts must preserve expected IDs/parents under concurrency and
  streaming. Exercise cancellation, errors, retries, cache hits, sampling,
  delayed/failed export, and content opt-out on both sides. Dual capture must yield
  exactly one canonical logical model record and evaluation input, with correct
  attempts and costs, including reversed delivery order and repeated exports.
  If that cannot be verified, publish only the single-authority recipes above.

No gateway configuration, SDK instrumentation, or backend mapping was implemented
as part of this documentation research.
