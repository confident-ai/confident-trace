---
name: confident-otel
description: >
  Export raw OpenTelemetry traces from an AI application to Confident AI.
  TRIGGER when the user wants to send OpenTelemetry or OTLP traces/spans from an
  LLM app, agent, RAG pipeline, or chatbot to Confident AI; configure the
  Confident AI OTLP endpoint; set confident.span.* or confident.trace.*
  attributes; export AI-app traces without the confident-trace package; wire an
  OTLPSpanExporter, OpenTelemetry Collector, or vendor-neutral OTel SDK to
  Confident AI; or pick the US vs EU OTLP endpoint. Language-agnostic: the
  mechanism is OTLP attribute keys plus an exporter endpoint. DO NOT TRIGGER for
  DeepEval test suites, datasets, goldens, metrics, or deepeval test run (use
  the `deepeval` skill); for instrumentation with the confident-trace SDK and
  its integrations (use the `confident-tracing` skill); or for non-AI software
  such as web servers, CRUD backends, or infrastructure.
license: Apache-2.0
metadata:
  author: Confident AI
  version: "1.0.0"
  category: observability
  tags: "opentelemetry, otel, otlp, tracing, confident-ai, spans"
  compatibility: "Works with any OpenTelemetry SDK in any language. Requires CONFIDENT_API_KEY. Confident AI's direct OTLP endpoint is HTTP only; use OTLP/HTTP, not gRPC."
---

# Confident AI OpenTelemetry Export

Use this skill to instrument an **AI application**—an LLM app, agent, RAG
pipeline, or chatbot—with **raw OpenTelemetry** so its traces land in Confident
AI. No `confident-trace` package is needed. The job is exactly two things:
export to the correct Confident AI OTLP endpoint and set the `confident.*`
attributes Confident AI reads from each span.

## Scope: AI Applications Only

Instrument only agent loops and planning, LLM calls, retrieval/vector search,
and tool calls. Do not apply `confident.*` attributes to web servers, CRUD
backends, database layers, infrastructure, or non-AI spans. If the target has
no LLM, agent, retrieval, or tool-calling component, this skill does not apply.

## When to Use vs the Other Skills

- **This skill (`confident-otel`)**—vendor-neutral OTLP export and raw
  `confident.*` attributes without the confident-trace package.
- **`confident-tracing` skill**—automatic integrations and custom SDK spans in
  Python or TypeScript.
- **`deepeval` skill**—evaluation suites, datasets, metrics, and test runs.

## Prerequisites

- A project-scoped `CONFIDENT_API_KEY`.
- An OpenTelemetry SDK for the application's language.
- For Python, `opentelemetry-sdk` and
  `opentelemetry-exporter-otlp-proto-http`.
- Direct Confident AI Cloud export uses OTLP/HTTP, never gRPC.

## How It Works

Point an OTLP/HTTP traces exporter at Confident AI with the
`x-confident-api-key` header. Confident AI reads `confident.*` attributes from
the spans. Parent/child nesting, trace IDs, sampling, status, links, and context
propagation remain native OpenTelemetry concerns.

## Workflow

1. Confirm the target is an AI application, then inspect for an existing
   `TracerProvider`, exporter, Collector, or APM pipeline.
2. Choose the direct Cloud endpoint from the API key's region prefix. Read
   `references/endpoint-and-exporter.md`.
3. Wire or repoint an OTLP/HTTP exporter with the `x-confident-api-key` header.
   For Python, start from `templates/confident_otel_setup.py`.
4. If the process emits unrelated OpenTelemetry spans, isolate Confident AI
   export so only AI spans reach it.
5. Set `confident.span.*` fields on spans and `confident.trace.*` fields for the
   whole trace. Read `references/span-attributes.md` and
   `references/trace-attributes.md`.
6. JSON-encode objects and metadata; use native homogeneous primitive arrays
   for string lists.
7. If the app already emits `gen_ai.*` semantic-convention attributes, read
   `references/gen-ai-fallbacks.md` before adding redundant fields.
8. Flush after active work completes and verify the trace hierarchy in
   Confident AI.

## Core Principles

1. Instrument and export AI components only.
2. Prefer repointing an existing exporter over adding a duplicate pipeline.
3. The `confident.*` keys are language-neutral.
4. Direct Confident AI Cloud export always uses OTLP/HTTP. A Collector may use
   another protocol on its application-facing side.
5. Set `confident.span.type` explicitly when known; use `gen_ai.*` inference
   only as a fallback.
6. Never hardcode API keys or record secrets and unapproved sensitive content.
7. Preserve native OpenTelemetry trace IDs, parentage, sampling, status,
   resources, links, and propagation.

## References

| Topic                                                                       | File                                  |
| --------------------------------------------------------------------------- | ------------------------------------- |
| Endpoints, region selection, authentication, exporter wiring, and filtering | `references/endpoint-and-exporter.md` |
| Trace-level `confident.trace.*` attributes                                  | `references/trace-attributes.md`      |
| Span-level `confident.span.*` attributes and data-type rules                | `references/span-attributes.md`       |
| Standard OpenTelemetry `gen_ai.*` fallback behavior                         | `references/gen-ai-fallbacks.md`      |

## Templates

| Purpose                                              | Template                            |
| ---------------------------------------------------- | ----------------------------------- |
| Minimal Python OTLP exporter setup and example trace | `templates/confident_otel_setup.py` |
