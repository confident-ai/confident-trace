# Endpoint and Exporter

Where to export OpenTelemetry traces and how to authenticate so they land in
Confident AI.

## Endpoints

| Region          | Base endpoint                      | Direct exporter endpoint                     |
| --------------- | ---------------------------------- | -------------------------------------------- |
| Default (US/AU) | `https://otel.confident-ai.com`    | `https://otel.confident-ai.com/v1/traces`    |
| EU              | `https://eu.otel.confident-ai.com` | `https://eu.otel.confident-ai.com/v1/traces` |

A directly configured OTLP/HTTP traces exporter needs the `/v1/traces` suffix.
For `OTEL_EXPORTER_OTLP_ENDPOINT`, provide the base endpoint; standard OTel SDKs
append `/v1/traces`. `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, when used, is the
complete traces endpoint.

Choose the endpoint from `CONFIDENT_API_KEY`:

- `confident_eu_...`: EU.
- `confident_us_...`: default.
- Any other prefix: default; ask for the project region when uncertain.

## Authentication

Every direct request must carry:

```text
x-confident-api-key: <CONFIDENT_API_KEY>
```

Read the key from the environment. Never hardcode it.

## Transport

Confident AI's direct Cloud endpoint accepts OTLP/HTTP, not gRPC.

- Python: use
  `opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter`.
- OpenTelemetry Collector: use an `otlphttp` exporter for the
  Collector-to-Confident-AI hop.
- Other SDKs: choose their OTLP/HTTP protobuf exporter.

An application may send gRPC to its own Collector, but that Collector must send
OTLP/HTTP to Confident AI.

## Standard Environment Variables

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://otel.confident-ai.com"
export OTEL_EXPORTER_OTLP_HEADERS="x-confident-api-key=<CONFIDENT_API_KEY>"
```

## Python Exporter Wiring

```python
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

api_key = os.environ["CONFIDENT_API_KEY"]
base = (
    "https://eu.otel.confident-ai.com"
    if api_key.startswith("confident_eu_")
    else "https://otel.confident-ai.com"
)

provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint=f"{base}/v1/traces",
            headers={"x-confident-api-key": api_key},
        )
    )
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)
```

If the application already owns a provider, preserve it and add only the
Confident AI-bound processor. Do not register a second global provider.

## Other Languages

The wiring is the same in every OpenTelemetry SDK:

1. Construct an OTLP/HTTP traces exporter.
2. Set its URL to the complete `/v1/traces` endpoint.
3. Add the `x-confident-api-key` header.
4. Attach it through a batch span processor.
5. Emit native OTel spans with the documented `confident.*` attributes.

## Export Only AI Spans

Applications often emit unrelated HTTP, database, cache, and infrastructure
spans. Do not send those to Confident AI.

Prefer a dedicated provider or pipeline used only by AI instrumentation. When
AI and non-AI spans must share a provider, filter the Confident AI-bound
processor or exporter. Treat a span as AI-related when it has:

- `confident.span.type`;
- applicable `gen_ai.*` semantic-convention attributes; or
- a verified AI-framework span name or instrumentation scope.

Be careful when filtering intermediate spans. Dropping a parent while exporting
its child leaves a dangling parent ID. Re-parent retained AI spans to the
nearest retained ancestor, or remove the missing parent reference so the span
becomes a clean root. Prefer Collector-side filtering when it can preserve the
required trace structure.

## Lifecycle

The component that owns the provider owns shutdown. Finish active work and
streams before forcing a flush or shutdown so the batch processor can export
all completed spans.
