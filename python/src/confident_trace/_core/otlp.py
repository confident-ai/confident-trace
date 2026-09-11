"""Serialize our OTLP connection for a separately exporting SDK subprocess."""

from urllib.parse import quote

from opentelemetry.sdk import environment_variables as env


def child_environment(protocol, kwargs, *, compression=None):
    # Only called for exporters we construct. Arbitrary caller-supplied exporters
    # (including in-memory exporters) cannot be represented as an OTLP endpoint.
    endpoint = kwargs.get("endpoint")
    if not endpoint:
        return None
    values = {
        env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: endpoint,
        env.OTEL_EXPORTER_OTLP_TRACES_PROTOCOL: protocol,
        env.OTEL_EXPORTER_OTLP_TRACES_HEADERS: ",".join(
            f"{quote(key, safe='')}={quote(value, safe='')}"
            for key, value in kwargs.get("headers", {}).items()
        ),
    }
    if compression is not None:
        values[env.OTEL_EXPORTER_OTLP_TRACES_COMPRESSION] = compression
    if "timeout" in kwargs:
        # Python's exporter argument is seconds; the child uses OTel milliseconds.
        values[env.OTEL_EXPORTER_OTLP_TRACES_TIMEOUT] = str(kwargs["timeout"] * 1000)
    return values
