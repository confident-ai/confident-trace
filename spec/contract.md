# Language-neutral contract v1

## Transport and ownership

Standard OTLP traces, HTTP/protobuf by default, or gRPC with a configured endpoint.
Confident authentication uses `x-confident-api-key`. There is no Confident wire
format, REST fallback, or SDK trace aggregation. Third-party spans retain their
attributes, events, and scope schema URL, including unknown convention values,
except that enabled native integrations stamp `confident.span.integration` with
the canonical SDK/framework label. Package-owned integrations stamp it at span
creation. Labels are defined by the public Python `Integration` enum; they are
independent of `gen_ai.provider.name`. Claude CLI subprocess spans bypass this
Python processor and are not stamped.

Trace IDs, parentage, sampling, status, resources, links, and propagation belong
to OpenTelemetry. Inference spans are CLIENT; custom steps/tools are INTERNAL.
Package instrumentation uses semantic conventions **1.37.0** and scope schema
`https://opentelemetry.io/schemas/1.37.0`. Registry completeness does not imply
instrumentation of every operation or emission of every signal.

## Export selection

Only spans from the SDK's own scope or with a `confident.*`/`gen_ai.*` attribute or
`gen_ai.*` event are exported, plus their tracked local ancestors (including ended ancestors with active descendants); parent IDs are never
rewritten. Python `init(export_non_ai_spans=True)` and TypeScript `exportNonAiSpans: true`
export every span. Shared cases live in `spec/span-export-vectors.json`.

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
| confident.trace.thread_id / turn_id / user_id / customer_id | string |
| confident.trace.thread.id / user.id / customer.id | string |
| confident.trace.thread.tags | native string array |
| confident.trace.thread.metadata | JSON object string, or bounded marker |
| confident.trace.user.name / customer.name | string |
| confident.trace.environment | string |
| confident.span.input / output | JSON string for custom steps/tools |
| confident.span.content_truncated | boolean |
| confident.span.attachments | JSON object string: marker id to attachment |

Custom content fields (`confident.trace.*` and `confident.span.*` input, output,
metadata, and the other content fields) may carry image and PDF markers,
`[CONFIDENT:IMAGE|PDF:<32 hex id>]`, anywhere in their JSON. A marker is written
wherever an image or PDF `Media` value appears, or is formatted into a string.
The span's `confident.span.attachments` maps each marker id it carries to
`{"dataBase64", "mimeType"}` for inline bytes, or to `{"url", "mimeType"?}` for a
remote reference. One attribute covers every content field the span writes,
including trace fields on an entry span. Rewriting a field drops attachments only
that field named. Marker-shaped text naming no media the process formatted is
left unchanged. The receiver resolves markers across the whole trace, replacing
each with the stored file.

An audio `Media` value in a custom content field is written in place as
`{"mimeType": "audio/...", "dataBase64": "..."}`, or `{"mimeType", "url"}` for a
remote file, typically beside a message's text:
`{"role": "user", "content": "...", "audio": {...}}`. The receiver recognises
audio by its `audio/*` `mimeType` under any key, stores inline bytes, and keeps
`{"mimeType", "url"}`. Audio is never formatted into text.

Inline bytes, as attachments or audio values, share the span's media budget with
GenAI message parts and do not count toward the text limit. Media whose bytes
cannot travel (over budget, unreadable, or not an image, PDF, or audio type, or
audio formatted into a string) is written as an `<inline_data: <mime>, not
captured>` note, so every marker the SDK writes has an attachment on the same
span.

Structured thread, user, and customer fields emit individual dotted attributes.
Their `id` must match the shorthand `*_id`, which the SDK always writes
alongside the dotted ID. Properties may be supplied in separate updates on the
same entry span; the receiver materializes an entity once an ID is present.
Disabled or failing metadata capture omits dotted metadata while safe identity,
name, and tag fields remain available. Entity IDs must remain stable within a
trace because OTEL span attributes cannot delete properties written for an
earlier ID.

Trace-row fields belong to the entry span. `update_trace` targets the active entry,
falling back to the current OTel span. Explicit thread IDs also populate
`gen_ai.conversation.id` on Confident-owned spans, inherited by subsequent package
spans under that entry. On foreign spans, `update_trace(thread_id=...)` writes the
Confident shorthand and dotted ID while preserving native conversation attributes. AgentCore
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

Ancestor selection is monotonic. Ended unresolved ancestors remain eligible until
their tracked subtree finishes. Payload pressure (1,024 spans / estimated 16 MiB)
and explicit flush/shutdown conservatively export pending ancestors. Completed
selected spans are never delayed for an active root. Export-all bypasses ancestry
selection. No span identifiers, relationships, timestamps or payloads are rewritten.
