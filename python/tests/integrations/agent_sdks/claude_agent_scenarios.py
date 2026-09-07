import json
import os
import sys
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, query
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace.integrations.claude_agent_sdk import instrumentation


@pytest.fixture
def configured(monkeypatch):
    from opentelemetry.exporter.otlp.proto.http import trace_exporter

    # Exercise core OTLP option resolution without network traffic or credentials.
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", lambda **kwargs: exporter)
    ct.shutdown()
    rt = ct.init(
        endpoint="https://collector.invalid/v1/traces",
        api_key="offline-secret",
        headers={"x-extra": "a,b=c"},
        instrumentations=("claude_agent_sdk",),
    )
    yield rt, exporter
    ct.shutdown()


@pytest.fixture
def cli(tmp_path):
    executable = tmp_path / "offline-cli"
    executable.write_text(
        f"#!{sys.executable}\n"
        + Path(__file__).with_name("claude_cli_fixture.py").read_text()
    )
    executable.chmod(0o755)
    return executable


@pytest.mark.parametrize("client_mode", [False, True])
async def test_real_sdk_subprocess_configuration_and_parentage(
    configured, cli, tmp_path, client_mode
):
    capture = tmp_path / "child-env.json"
    options = ClaudeAgentOptions(
        cli_path=str(cli), env={"CT_ENV_CAPTURE": str(capture)}
    )
    original_env = dict(options.env)
    process_env = dict(os.environ)
    with ct.span("request") as root:
        if client_mode:
            async with ClaudeSDKClient(options=options) as client:
                await client.query("hello")
                messages = [message async for message in client.receive_response()]
        else:
            messages = [
                message async for message in query(prompt="hello", options=options)
            ]
    (result,) = [message for message in messages if isinstance(message, ResultMessage)]
    assert result.result == "hello" and not result.is_error
    env = json.loads(capture.read_text())
    assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
    assert env["CLAUDE_CODE_ENHANCED_TELEMETRY_BETA"] == "1"
    assert env["OTEL_TRACES_EXPORTER"] == "otlp"
    assert (
        env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
        == "https://collector.invalid/v1/traces"
    )
    assert (
        env["OTEL_EXPORTER_OTLP_TRACES_HEADERS"]
        == "x-confident-api-key=offline-secret,x-extra=a%2Cb%3Dc"
    )
    assert env["TRACEPARENT"].split("-")[1:3] == [
        f"{root.get_span_context().trace_id:032x}",
        f"{root.get_span_context().span_id:016x}",
    ]
    assert "OTEL_METRICS_EXPORTER" not in env and "OTEL_LOGS_EXPORTER" not in env
    assert options.env == original_env
    assert dict(os.environ) == process_env


@pytest.mark.parametrize(
    "override",
    [
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://other.invalid"},
        {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://other.invalid/traces"},
        {"OTEL_EXPORTER_OTLP_TRACES_HEADERS": "authorization=other"},
        {"CLAUDE_CODE_ENABLE_TELEMETRY": "0"},
        {"CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "false"},
        {"OTEL_TRACES_EXPORTER": "none"},
        {"OTEL_SDK_DISABLED": "true"},
        {"BETA_TRACING_ENDPOINT": "https://other.invalid"},
    ],
)
def test_native_overrides_do_not_receive_confident_credentials(configured, override):
    options = ClaudeAgentOptions(env=override)
    transport = SubprocessCLITransport("hello", options)
    effective = transport._options.env
    assert all(effective[key] == value for key, value in override.items())
    assert "offline-secret" not in str(effective)
    assert options.env == override


async def test_explicit_parent_wins(configured, cli, tmp_path):
    parent = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    capture = tmp_path / "explicit-env.json"
    options = ClaudeAgentOptions(
        cli_path=str(cli),
        env={
            "CT_ENV_CAPTURE": str(capture),
            "TRACEPARENT": parent,
            "TRACESTATE": "tenant=other",
        },
    )
    with ct.span("request"):
        messages = [message async for message in query(prompt="hello", options=options)]
    assert isinstance(messages[-1], ResultMessage)
    env = json.loads(capture.read_text())
    assert env["TRACEPARENT"] == parent and env["TRACESTATE"] == "tenant=other"


def test_custom_exporter_does_not_invent_child_destination():
    ct.shutdown()
    ct.init(exporter=InMemorySpanExporter(), instrumentations=("claude_agent_sdk",))
    try:
        options = ClaudeAgentOptions()
        transport = SubprocessCLITransport("hello", options)
        assert transport._options is options and options.env == {}
    finally:
        ct.shutdown()


def test_shutdown_reinit_and_later_patch(configured, monkeypatch):
    original = vars(SubprocessCLITransport)["__init__"].__wrapped__
    first = vars(SubprocessCLITransport)["__init__"]
    ct.shutdown()
    assert vars(SubprocessCLITransport)["__init__"] is original
    ct.init(exporter=InMemorySpanExporter(), instrumentations=("claude_agent_sdk",))
    second = vars(SubprocessCLITransport)["__init__"]
    assert second is not first and second is not original
    import wrapt

    later = wrapt.FunctionWrapper(
        second, lambda wrapped, instance, args, kwargs: wrapped(*args, **kwargs)
    )
    try:
        SubprocessCLITransport.__init__ = later
        ct.shutdown()
        assert vars(SubprocessCLITransport)["__init__"] is later
    finally:
        SubprocessCLITransport.__init__ = original


def test_configuration_failure_is_fail_open(configured, monkeypatch):
    calls = []

    def failed(*args):
        calls.append(1)
        raise ValueError("unavailable")

    monkeypatch.setattr(instrumentation, "configured_options", failed)
    options = ClaudeAgentOptions()
    transport = SubprocessCLITransport("hello", options)
    assert transport._options is options
    assert calls == [1]


def test_disabled_does_not_patch(monkeypatch):
    ct.shutdown()
    original = vars(SubprocessCLITransport)["__init__"]
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert not ct.init(
        exporter=InMemorySpanExporter(), instrumentations=("claude_agent_sdk",)
    ).active
    assert vars(SubprocessCLITransport)["__init__"] is original
