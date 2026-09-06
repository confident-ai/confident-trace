from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from confident_trace import init, shutdown, span

provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
init(tracer_provider=provider, instrumentations=())
with span("standard OTel") as current:
    current.set_attribute("example", True)
shutdown()  # only Confident's processor
provider.shutdown()  # application owns its provider
