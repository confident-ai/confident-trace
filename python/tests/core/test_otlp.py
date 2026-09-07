"""Native child export resolution, separate from the host exporter lifecycle."""

import pytest

from confident_trace._core.otlp import child_environment


@pytest.mark.parametrize(
    "protocol,expected",
    [
        ("http/protobuf", "https://collector.invalid/base/v1/traces"),
        ("grpc", "https://collector.invalid/base/"),
    ],
)
def test_child_export_connection_resolution(monkeypatch, protocol, expected):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://collector.invalid/base/")
    config = child_environment(
        protocol,
        {"headers": {"authorization": "a,b=c %"}, "timeout": 2},
        compression="gzip",
    )
    assert config == {
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": expected,
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": protocol,
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "authorization=a%2Cb%3Dc%20%25",
        "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT": "2000",
        "OTEL_EXPORTER_OTLP_TRACES_COMPRESSION": "gzip",
    }
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://signal.invalid/exact"
    )
    assert (
        child_environment(protocol, {})["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
        == "https://signal.invalid/exact"
    )
    assert (
        child_environment(protocol, {"endpoint": "https://explicit.invalid/exact"})[
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
        ]
        == "https://explicit.invalid/exact"
    )
