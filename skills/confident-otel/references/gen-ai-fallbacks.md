# GenAI Semantic-Convention Fallbacks

When a `confident.*` attribute is absent, Confident AI can fall back to standard
OpenTelemetry GenAI semantic-convention attributes (`gen_ai.*`). Read this file
only when the application already emits `gen_ai.*` spans. For new raw
instrumentation, set the `confident.*` attributes directly.

## Span-Type Inference

| Condition                                                                   | Inferred `confident.span.type` |
| --------------------------------------------------------------------------- | ------------------------------ |
| `gen_ai.operation.name` is `chat`, `generate_content`, or `text_completion` | `llm`                          |
| `gen_ai.tool.name` is present                                               | `tool`                         |
| Otherwise                                                                   | `base`                         |

## Attribute Fallbacks

| Confident attribute                | GenAI fallback               |
| ---------------------------------- | ---------------------------- |
| `confident.llm.model`              | `gen_ai.request.model`       |
| `confident.llm.input_token_count`  | `gen_ai.usage.input_tokens`  |
| `confident.llm.output_token_count` | `gen_ai.usage.output_tokens` |
| `confident.tool.name`              | `gen_ai.tool.name`           |

`confident.*` values win when both forms are present. Rely on fallbacks only to
avoid duplicating attributes an existing GenAI integration already emits.
