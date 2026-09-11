# Span-Level Attributes

Span-level attributes describe one AI component. Set
`confident.span.type` first, then fields shared by all spans and fields for the
selected type.

The raw OTLP span types are `llm`, `tool`, `agent`, `retriever`, and `base`.
Apply them only to AI spans.

## Common Span Attributes

| Attribute                          | Type        | Notes                                              |
| ---------------------------------- | ----------- | -------------------------------------------------- |
| `confident.span.type`              | string      | `llm`, `tool`, `agent`, `retriever`, or `base`.    |
| `confident.span.name`              | string      | Display-name override for the native span name.    |
| `confident.span.input`             | string      | JSON-encode non-string values.                     |
| `confident.span.output`            | string      | JSON-encode non-string values.                     |
| `confident.span.metadata`          | JSON string | JSON-encoded object.                               |
| `confident.span.context`           | string list | Ground-truth context.                              |
| `confident.span.retrieval_context` | string list | Retrieved chunks.                                  |
| `confident.span.expected_output`   | string      | Expected output for test-case-style evals.         |
| `confident.span.tools_called`      | string list | Each item is one JSON-serialized tool call.        |
| `confident.span.expected_tools`    | string list | Each item is one JSON-serialized tool call.        |
| `confident.span.metric_collection` | string      | Server-side metric collection to run on this span. |

## Errors

Use native OpenTelemetry status and exception recording, not a
`confident.*` error field:

```python
from opentelemetry.trace import Status, StatusCode

try:
    ...
except Exception as exc:
    span.set_status(Status(StatusCode.ERROR), str(exc))
    span.record_exception(exc)
    raise
```

## LLM Spans

Set `confident.span.type` to `llm`.

| Attribute                             | Type   | Notes                                         |
| ------------------------------------- | ------ | --------------------------------------------- |
| `confident.llm.model`                 | string | Model name; fallback: `gen_ai.request.model`. |
| `confident.span.provider`             | string | Provider; may be inferred from the model.     |
| `confident.llm.input_token_count`     | int    | Fallback: `gen_ai.usage.input_tokens`.        |
| `confident.llm.output_token_count`    | int    | Fallback: `gen_ai.usage.output_tokens`.       |
| `confident.llm.cost_per_input_token`  | float  | Cost per input token.                         |
| `confident.llm.cost_per_output_token` | float  | Cost per output token.                        |

For a prompt managed in Confident AI, set the applicable discrete fields:

- `confident.span.prompt_alias`
- `confident.span.prompt_version`
- `confident.span.prompt_commit_hash`
- `confident.span.prompt_label`

## Agent Spans

Set `confident.span.type` to `agent`.

| Attribute                         | Type        |
| --------------------------------- | ----------- |
| `confident.agent.name`            | string      |
| `confident.agent.available_tools` | string list |
| `confident.agent.agent_handoffs`  | string list |

## Retriever Spans

Set `confident.span.type` to `retriever`.

| Attribute                        | Type   |
| -------------------------------- | ------ |
| `confident.retriever.embedder`   | string |
| `confident.retriever.top_k`      | int    |
| `confident.retriever.chunk_size` | int    |

Put retrieved chunks on `confident.span.retrieval_context`.

## Tool Spans

Set `confident.span.type` to `tool`.

| Attribute                    | Type   | Notes                         |
| ---------------------------- | ------ | ----------------------------- |
| `confident.tool.name`        | string | Fallback: `gen_ai.tool.name`. |
| `confident.tool.description` | string | Human-readable description.   |

Put tool arguments on `confident.span.input` and the result on
`confident.span.output`.

## Data-Type Rules

OpenTelemetry attributes must be primitives or homogeneous primitive lists:

- JSON-encode objects and dictionaries, including metadata.
- Native OTLP string arrays are valid for tags, context, retrieval context,
  available tools, and handoffs.
- For tool-call lists, use an OTLP string array where each item is one
  JSON-serialized tool call—not one JSON string containing the whole list.
- JSON-encode non-string input and output values.
- Set counts and costs as native numbers, not strings.

## Span Nesting

Parent/child relationships come from native OpenTelemetry context. Start a child
inside its parent's active context; do not express parenthood through
attributes.
