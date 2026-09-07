"""No API keys or framework packages needed: print an ordinary OTel trace locally."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter

import confident_trace as ct


@ct.span(kind="tool")
def lookup(value: str) -> str:
    return value.upper()


def main():
    provider = TracerProvider()
    ct.init(
        tracer_provider=provider,
        exporter=ConsoleSpanExporter(),
        instrumentations=(),
    )
    try:
        with ct.span("request", thread_id="local-example", input="hello"):
            result = lookup("hello")
            ct.update_trace(output=result)
            print(result)
    finally:
        ct.shutdown()
        provider.shutdown()


if __name__ == "__main__":
    main()
