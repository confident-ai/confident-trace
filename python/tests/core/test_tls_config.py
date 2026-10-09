"""TLS initialization choices also govern deferred recording uploads."""

import ssl
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace.integrations.livekit.recording import _upload


@pytest.mark.asyncio
@pytest.mark.parametrize("env", [None, "TRUE", "true"])
@pytest.mark.parametrize("explicit", [None, False, True])
async def test_recording_uses_init_tls_setting(monkeypatch, tmp_path, env, explicit):
    ct.shutdown()
    if env is None:
        monkeypatch.delenv("CONFIDENT_OTEL_TLS_SKIP_VERIFY", raising=False)
    else:
        monkeypatch.setenv("CONFIDENT_OTEL_TLS_SKIP_VERIFY", env)
    monkeypatch.delenv("LIVEKIT_TELEMETRY_ALLOW_PII", raising=False)
    runtime = ct.init(instrumentations=(), tls_skip_verify=explicit)
    expected = explicit if explicit is not None else env == "true"
    path = tmp_path / "audio.ogg"
    path.write_bytes(b"OggS-recording")
    report = SimpleNamespace(audio_recording_path=path, audio_recording_started_at=1)
    ctx = SimpleNamespace(make_session_report=lambda: report)
    contexts = []

    def urlopen(request, timeout, context):
        contexts.append(context)
        raise OSError("not sent")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setenv("CONFIDENT_OTEL_TLS_SKIP_VERIFY", str(not expected).lower())
    try:
        await _upload(runtime, ctx, "a" * 32)
        assert len(contexts) == 1
        if expected:
            assert contexts[0].verify_mode == ssl.CERT_NONE
            assert not contexts[0].check_hostname
        else:
            assert contexts[0] is None
    finally:
        ct.shutdown()


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_invalid_tls_option_disables_initialization(value):
    ct.shutdown()
    try:
        assert not ct.init(tls_skip_verify=value, instrumentations=()).active
    finally:
        ct.shutdown()


def test_custom_exporter_is_preserved():
    ct.shutdown()
    exporter = InMemorySpanExporter()
    try:
        runtime = ct.init(exporter=exporter, tls_skip_verify=True, instrumentations=())
        assert runtime.active
        assert runtime.processor.delegate.span_exporter is exporter
        assert runtime.otlp_http_export is None
    finally:
        ct.shutdown()
