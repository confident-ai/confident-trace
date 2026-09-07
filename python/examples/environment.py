"""Configure exports entirely through environment variables before launching.

CONFIDENT_API_KEY=... \\
OTEL_RESOURCE_ATTRIBUTES='service.name=my-agent' \\
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT='http://localhost:4318/v1/traces' \\
python python/examples/environment.py

Use OTEL_SDK_DISABLED=true to disable package instrumentation/export at startup.
For a gRPC Collector, set OTEL_EXPORTER_OTLP_PROTOCOL=grpc and
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317. No application-code change.
"""

import os

from openai import OpenAI

from confident_trace import init

init()
client = OpenAI()
response = client.responses.create(
    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), input="Hello"
)
print(response.output_text)
