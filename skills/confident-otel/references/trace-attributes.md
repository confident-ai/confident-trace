# Trace-Level Attributes

Trace-level attributes describe the whole trace. Set them as
`confident.trace.*` attributes on any span in the trace, usually the root span.
Confident AI aggregates them to the trace.

| Attribute                           | Type        | Notes                                                                  |
| ----------------------------------- | ----------- | ---------------------------------------------------------------------- |
| `confident.trace.name`              | string      | Human-readable trace name.                                             |
| `confident.trace.input`             | string      | JSON-encode non-string values.                                         |
| `confident.trace.output`            | string      | JSON-encode non-string values.                                         |
| `confident.trace.user_id`           | string      | End-user or customer identifier.                                       |
| `confident.trace.thread_id`         | string      | Conversation or session thread.                                        |
| `confident.trace.turn_id`           | string      | Turn identifier.                                                       |
| `confident.trace.tags`              | string list | Grouping labels.                                                       |
| `confident.trace.metadata`          | JSON string | JSON-encoded object.                                                   |
| `confident.trace.environment`       | string      | Deployment environment. Set it explicitly; see Environment Resolution. |
| `confident.trace.retrieval_context` | string list | Retrieved chunks or documents.                                         |
| `confident.trace.context`           | string list | Ground-truth context.                                                  |
| `confident.trace.expected_output`   | string      | Expected output for test-case-style evals.                             |
| `confident.trace.tools_called`      | string list | Each item is one JSON-serialized tool call.                            |
| `confident.trace.expected_tools`    | string list | Each item is one JSON-serialized tool call.                            |
| `confident.trace.test_case_id`      | string      | Related test-case ID.                                                  |
| `confident.trace.metric_collection` | string      | Server-side metric collection to run on the trace.                     |

All fields are optional. Set only what is meaningful.

## Environment Resolution

`confident.trace.environment` can be a span attribute or an OpenTelemetry
Resource attribute. The Resource value wins and is the preferred way to stamp
an environment once for the process:

```bash
export OTEL_RESOURCE_ATTRIBUTES="confident.trace.environment=production"
```

Common values are `production`, `staging`, `development`, and `testing`. Always
set the environment explicitly rather than relying on a backend default.

## Data Types

The encoding rules from `span-attributes.md` apply:

- Metadata is a JSON object string.
- Tags, context, and retrieval context are native OTLP string arrays.
- Tool-call fields are string arrays whose individual elements are
  JSON-serialized tool calls.
- Non-string input and output values are JSON-encoded.
