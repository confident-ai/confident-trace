# Language-neutral contract v1

## Transport and ownership

Standard OTLP traces, HTTP/protobuf by default, or gRPC with a configured endpoint.
Confident authentication uses `x-confident-api-key`. There is no Confident wire
format, REST fallback, or SDK trace aggregation. Third-party spans retain their
attributes, events, and scope schema URL, including unknown convention values.

Trace IDs, parentage, sampling, status, resources, links, and propagation belong
to OpenTelemetry. Inference spans are CLIENT; custom steps/tools are INTERNAL.
Package instrumentation uses semantic conventions **1.37.0** and scope schema
`https://opentelemetry.io/schemas/1.37.0`. Registry completeness does not imply
instrumentation of every operation or emission of every signal.

## Convention source and release audit

`genai/1.37.0.json` is the language-neutral snapshot. It contains resolved attribute
and operation requirements, known enum values, metrics/events, original message
schemas, and attributed upstream source text with checksums. The upstream tag and
commit identify the exact release; this version is distinct from both the OTel SDK
version and the Confident SDK version.

Published snapshots are immutable. Add a new versioned snapshot for convention
upgrades. `releases/python-0.1.0.json` binds the SDK release to the registry hash,
content representation, integration coverage, dependency matrix, and deviations.
See [registry maintenance](genai/README.md) for reproducible generation and CI checks.

## Content and trace fields

Package-owned input/output attributes contain JSON arrays following the pinned
message schemas. Output messages include `finish_reason`; the empty string means
the provider has not supplied a reason, including early-closed streams. It does
not assert success. Error status and `error.type` retain their normal OTel meaning.

Capture is enabled by default, with explicit opt-out. Redaction precedes bounded
serialization. Invalid shapes or failing redactors omit content. Oversized message
arrays retain a valid prefix by shortening text and removing complete parts or
messages. Unknown content and multimodal payloads are represented with explicit
omission markers; binary payloads are not captured. Custom arbitrary values may
use the JSON string `[truncated]`, but message attributes never use that shape.

Streaming keeps bounded per-candidate state. Usage and finish-reason processing
continue after the content budget is exhausted. No iterator is consumed ahead
of the application. `confident.span.content_truncated` marks accumulator overflow.

| Attribute | Encoding |
|---|---|
| confident.trace.name | string |
| confident.trace.input / output | JSON string |
| confident.trace.tags | native string array |
| confident.trace.metadata | JSON object string, or bounded marker |
| confident.trace.thread_id / turn_id / user_id | string |
| confident.trace.environment | string |
| confident.span.input / output | JSON string for custom steps/tools |
| confident.span.content_truncated | boolean |

Trace-row fields belong to the entry span. `update_trace` targets the active entry,
falling back to the current OTel span. Explicit thread IDs also populate
`gen_ai.conversation.id` on Confident-owned spans, inherited by subsequent package
spans under that entry. On foreign spans, `update_trace(thread_id=...)` writes only
`confident.trace.thread_id` and preserves native conversation attributes. AgentCore
request middleware likewise enriches its span with the Confident thread extension.
Provider-supplied conversation IDs populate the inference span and, when it is the
entry, the Confident thread field. No conversation is inferred from prompt text.
Metadata never changes OTel parentage, starts a turn scope, or merges traces.

Tool spans use `execute_tool`, a tool name, and the standard tool span naming
convention. Custom tool arguments/results use Confident fields: the newer
`gen_ai.tool.call.arguments/result` attributes are absent in 1.37.0.

## Receiver handoff (implemented separately)

`genai-vectors.json` describes current and legacy content shapes, mixed-version
batches, and duplicate-representation precedence. These fixtures express receiver
requirements, not a claim that this SDK implements or deploys backend mapping.

A receiver should resolve input and output independently. A valid message-array
attribute wins, including an empty array; absent or malformed attributes fall back
to the corresponding legacy events. Do not concatenate both representations.
Modern GenAI log events are a separate signal, not legacy span events.

`genai-emission-vectors.json` describes this package's supported provider
extraction. Python tests validate its emitted OTLP and upstream message schemas.
Future languages consume the same registry and vectors. SDK CI is independent of
backend code, receiver deployment, and ClickHouse storage.
