---
name: confident-tracing
description: >
  Instrument an AI application with the confident-trace SDK so its behavior is
  visible in Confident AI. TRIGGER when the user wants to add Confident Trace,
  automatic AI instrumentation, @span, span(), or withSpan() to an LLM app,
  agent, RAG pipeline, or chatbot; wire a framework, model-provider, agent SDK,
  or gateway integration; choose between automatic integration and custom
  instrumentation; set span types, tags, metadata, or conversation context; or
  send confident-trace SDK traces to Confident AI. DO NOT TRIGGER for DeepEval
  test suites, datasets, goldens, metrics, or deepeval test run (use the
  `deepeval` skill), or for raw OpenTelemetry / OTLP export without the
  confident-trace package (use the `confident-otel` skill). This skill is
  purely confident-trace SDK instrumentation—producing well-formed traces, not
  running evals.
license: Apache-2.0
metadata:
  author: Confident AI
  version: "1.0.0"
  category: observability
  tags: "confident-trace, tracing, opentelemetry, instrumentation, agents, spans"
  compatibility: "Python 3.10+ or Node.js 22+ with the confident-trace package. Sending traces to Confident AI requires CONFIDENT_API_KEY."
---

# Confident Trace

Use this skill to instrument an **AI application**—an LLM app, agent, RAG
pipeline, or chatbot—with the **confident-trace SDK** so its execution is
visible span by span in Confident AI. Pick a supported integration when one
exists, fall back to custom spans otherwise, give each span a meaningful type,
and add useful trace context.

This skill stops at producing well-formed traces. Attaching evaluation metrics
and running evals is the `deepeval` skill's job.

## Scope: AI Applications Only

Instrument only the AI parts of the system: agent loops and planning, LLM
calls, retrieval/vector search, and tool calls. Do not trace non-AI software
such as web servers, CRUD backends, database layers, or infrastructure. If the
target has no LLM, agent, retrieval, or tool-calling component, this skill does
not apply.

## When to Use vs the `deepeval` and `confident-otel` Skills

- **This skill (`confident-tracing`)**—instrument a Python or TypeScript app
  with the confident-trace SDK and its integrations.
- **`deepeval` skill**—build pytest evaluation suites: datasets, metrics,
  traced evals, `deepeval test run`, and iteration.
- **`confident-otel` skill**—export raw, vendor-neutral OpenTelemetry traces to
  Confident AI without the confident-trace package.

## Prerequisites

- Python 3.10+ or Node.js 22+ with `confident-trace` installed.
- A project-scoped `CONFIDENT_API_KEY` for export to Confident AI.

## Workflow

1. Confirm the target is an AI application. If it has no LLM, agent, retrieval,
   or tool-calling component, stop.
2. Detect the language, framework, model provider, agent SDK, gateway, bundler,
   and existing OpenTelemetry setup.
3. Read `references/integrations.md` and the exact repository integration
   documentation for what was detected. Prefer automatic instrumentation.
4. If no integration fits, or the code owns a meaningful outer boundary,
   instrument it with a custom span. Read `references/tracing.md`.
5. Give each span a meaningful type (`llm`, `retriever`, `tool`, `agent`, or
   `custom`) and capture useful inputs and outputs.
6. Add trace-level tags, metadata, user ID, thread ID, turn ID, and environment
   where they help diagnose failure patterns.
7. Never trace secrets, credentials, or unapproved sensitive data. Disable
   package-owned content capture when required.
8. Finish active work and streams before flushing or shutting down, then verify
   the trace hierarchy in Confident AI.

## Core Principles

1. Instrument AI components only.
2. Prefer a supported integration over custom instrumentation.
3. Read the exact integration documentation before writing tracing code.
4. Initialize once, before provider or framework calls. TypeScript automatic
   instrumentation requires both the Node preload and `init()`.
5. Do not wrap calls already covered by an integration.
6. Use `turn()` for conversation turns that must start separate traces; a
   thread ID associates turns but does not merge their OpenTelemetry traces.
7. Producing traces is the scope. Evals belong to `deepeval`; raw
   OpenTelemetry export belongs to `confident-otel`.

## References

| Topic                                                                     | File                         |
| ------------------------------------------------------------------------- | ---------------------------- |
| SDK instrumentation: initialization, custom spans, context, and lifecycle | `references/tracing.md`      |
| Integration selection and supported provider/framework index              | `references/integrations.md` |
