"""Raw OpenTelemetry -> Confident AI: minimal setup and example trace.

Requires:
    pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
    export CONFIDENT_API_KEY="..."

Run this file to smoke-test direct OTLP/HTTP export. Replace the example
attributes with values from the real application before using it in production.
"""

import json
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode


def pick_endpoint(api_key: str) -> str:
    """Return the direct Cloud base endpoint for the API key's region."""
    if api_key.startswith("confident_eu_"):
        return "https://eu.otel.confident-ai.com"
    return "https://otel.confident-ai.com"


def configure_tracing() -> trace.Tracer:
    """Create a provider that exports raw OTel spans to Confident AI."""
    api_key = os.environ.get("CONFIDENT_API_KEY")
    if not api_key:
        raise SystemExit("Set CONFIDENT_API_KEY before running this example.")

    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{pick_endpoint(api_key)}/v1/traces",
                headers={"x-confident-api-key": api_key},
            )
        )
    )
    trace.set_tracer_provider(provider)
    return trace.get_tracer(__name__)


def run_example(tracer: trace.Tracer) -> None:
    """Emit an agent trace containing one child LLM span."""
    with tracer.start_as_current_span("support-agent") as root:
        root.set_attribute("confident.span.type", "agent")
        root.set_attribute("confident.agent.name", "support-agent")
        root.set_attribute("confident.span.input", "Where is my order?")

        root.set_attribute("confident.trace.name", "support-chat")
        root.set_attribute("confident.trace.input", "Where is my order?")
        root.set_attribute("confident.trace.tags", ["support", "example"])
        root.set_attribute(
            "confident.trace.metadata",
            json.dumps({"app_version": "1.0.0", "route": "order_status"}),
        )

        with tracer.start_as_current_span("chat-completion") as llm:
            llm.set_attribute("confident.span.type", "llm")
            llm.set_attribute("confident.llm.model", "gpt-4o")
            llm.set_attribute("confident.llm.input_token_count", 42)
            llm.set_attribute("confident.llm.output_token_count", 18)
            llm.set_attribute(
                "confident.span.metadata",
                json.dumps({"temperature": 0.2}),
            )

            try:
                answer = "Your order ships tomorrow."
                llm.set_attribute("confident.span.output", answer)
            except Exception as exc:
                llm.set_status(Status(StatusCode.ERROR), str(exc))
                llm.record_exception(exc)
                raise

        root.set_attribute("confident.span.output", answer)
        root.set_attribute("confident.trace.output", answer)


if __name__ == "__main__":
    tracer = configure_tracing()
    run_example(tracer)
    trace.get_tracer_provider().shutdown()
    print("Trace exported. Check Confident AI.")
