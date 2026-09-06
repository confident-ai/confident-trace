# Python architecture

Users call `init()` to configure export and instrument supported installed SDKs.
Optional `@span` creates custom steps. Provider-specific processing lives in
`src/confident_trace/integrations/<integration>/`:

| Package | SDK surface |
| --- | --- |
| `openai` | Chat Completions and Responses |
| `anthropic` | Messages, including lazy stream managers and final-message helpers |
| `google_genai` | Content generation |
| `bedrock` | Boto3 Bedrock Runtime Converse and ConverseStream event streams |

Provider wrapper packages contain:

- `instrumentation.py`: method targets and `install(runtime)`, request setup, and result dispatch.
- `extraction.py`: SDK request/response fields, provider identity, message conversion, and attributes.
- `streaming.py`: SDK events and provider-specific stream helpers, using bounded shared accumulation.

## Data flow

```text
init() → _bootstrap → _core.runtime: provider, processor, exporter configuration
                   → integrations.registry: load selected integrations
                       → integration.install(runtime): install owned patches

SDK call → integration.instrumentation → shared call lifecycle → core Operation
         → integration.extraction: request attributes and content
SDK result/events → integration.extraction/streaming → span attributes
span ends → standard OTel processor → standard OTLP exporter
shutdown() → remove owned patches and close owned resources
```

The registry imports only selected integration packages. Target SDK imports happen
when installing that integration; missing optional SDKs are harmless. Repeated
initialization keeps the existing runtime. Cleanup restores a patched method only
if the installed wrapper still owns that method, preserving subsequent wrappers.

## Shared infrastructure

`_core/runtime.py` owns configuration and OTel resource lifecycle. `_bootstrap.py`
connects it to the registry; core does not import provider packages.
`_core/spans.py` owns the public decorator/context manager, trace metadata, and
span lifetime. `content.py` handles redaction, serialization, and bounds;
`safety.py` handles fail-open telemetry calls.

`integrations/_shared/patching.py` manages patch ownership. `lifecycle.py` manages
common sync/async calls using provider callbacks. `streams.py` attaches context
while driving iterators and handles completion, close, and errors. It contains no
Anthropic stream helpers or Botocore event parsing. `accumulation.py` stores bounded
text, tool arguments, and candidate completion state without interpreting SDK
events. Integrations extract usage into standard attributes even when the content
budget is exhausted. `extraction.py` supplies safe value-access utilities.

Integrations may import core, generated constants, and shared infrastructure, but
never another integration. Bedrock feeds neutral accumulator operations directly;
it does not synthesize OpenAI chunks. Keep provider field interpretation out of
core and shared streaming code.

## Adding an integration

1. Add its package with an `install(runtime)` function returning cleanup callbacks.
   SDK wrappers can use shared patching and lifecycle helpers. An integration for
   a framework that already emits OTel can configure its native instrumentor and
   return its cleanup callback without adding wrappers.
2. Register its lazy module path in `integrations/registry.py`. Update the default
   selection in `_bootstrap.py` only when automatic enablement is intended.
3. Keep request/response and event parsing in that package. Use generated constants
   from `_semconv/genai_v1_37_0.py`; preserve third-party OTel telemetry as emitted.
4. Add mocked SDK transport tests under `tests/integrations/<provider>/`, including
   exceptions, streaming completion/close, and async behavior where supported.
   Reusable exporters and schema checks are in `tests/conftest.py`; shared lifecycle
   tests live in `tests/core/`, and language-neutral fixture checks in `tests/contracts/`.
5. Add small provider examples and update integration coverage and the release
   manifest. Run both dependency suites, generator checks, packaging, and benchmarks.

The source registry, release manifests, and shared fixtures remain at repository
root under `spec/`. Run `python tools/generate_genai.py` from the repository root to
regenerate Python constants and compatibility documentation; use `--check` in CI.
Do not hand-edit generated constants or published convention snapshots.

These module paths are private implementation details. Public imports remain
`from confident_trace import init, span, update_trace, flush, shutdown`.


## Native integrations

`google_adk` registers a verified native inference scope with shared lifecycle
code. The generic provider wrapper checks only the current scope and operation;
it does not import ADK or parse ADK payloads. Shutdown removes the registration.
`agentcore` wraps the runtime application's ASGI call boundary with upstream OTel
middleware only when no active server span covers it. Middleware is cached on the
application and becomes inactive at shutdown; a new initialization replaces it.
Both adapters use the same lazy registry and owned-cleanup contract as providers.
Native integrations do not need extraction or streaming modules: their frameworks
own content, stream lifecycle, convention versions, and span production.


## Telemetry vocabulary ownership

- `_semconv/genai_v1_37_0.py` is generated from the immutable snapshot and defines
  what Confident-owned instrumentation emits. It remains the emission contract.
- `_semconv/native.py` contains the small read-only recognition vocabulary used
  for native inference deduplication. It does not select a schema or import the
  generated emission vocabulary. Native scope versions and unknown values pass
  through unchanged; new compatibility aliases require evidence and tests.
- Integration `_constants.py` modules hold framework-owned identifiers such as
  ADK's instrumentation scope; they are not OTel attribute definitions.
- `_attributes.py` owns Confident extensions, its instrumentation identity, and
  explicit public-metadata-to-attribute mappings. Enrichment of foreign spans uses
  those extensions. `update_trace(thread_id=...)` also writes the pinned GenAI
  conversation attribute only when updating a Confident-owned span.
- OTel configuration keys come from the SDK's environment-variable constants.
  These configuration names are independent of telemetry schema versions.

Runtime code uses named string constants rather than closed enums. Multiple native
conventions may coexist in one trace/export batch; Confident does not relabel them
as its own schema. Architecture tests reject inline telemetry names and dynamic
namespace prefixes outside definition modules. Wire fixtures and tests retain
independent literals so they can detect incorrect constant values.
