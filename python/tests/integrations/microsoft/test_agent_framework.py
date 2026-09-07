"""Run native framework checks without importing or configuring it in pytest's process."""

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from conftest import ROOT

HERE = Path(__file__).parent


def scenario_environment():
    # Allow only OS/interpreter essentials. In particular do not inherit OTel,
    # Azure, framework flags, credentials, pytest plugins, or tracing autoloaders.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "HOME",
        "USERPROFILE",
        "TMP",
        "TEMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(
        PYTHONPATH=str(ROOT / "python/src"),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        ENABLE_INSTRUMENTATION="false",
        ENABLE_SENSITIVE_DATA="false",
    )
    return env


@pytest.fixture
def microsoft_environment():
    for package in ("agent-framework-core", "agent-framework-openai"):
        try:
            version(package)
        except PackageNotFoundError:
            pytest.skip(f"{package} is not installed")
    return scenario_environment()


@pytest.mark.parametrize(
    "scenario",
    [
        "test_agents_streams_sessions_and_direct_calls[buffered-local]",
        "test_agents_streams_sessions_and_direct_calls[stream-local]",
        "test_agents_streams_sessions_and_direct_calls[buffered-service]",
        "test_agents_streams_sessions_and_direct_calls[stream-service]",
        "test_tool_calls_keep_their_provider_spans",
        "test_workflow_parentage",
        "test_failure_and_cancellation[False]",
        "test_failure_and_cancellation[True]",
    ],
)
def test_framework_scenario(scenario, microsoft_environment, tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "pytest_asyncio.plugin",
            f"{HERE / 'agent_framework_scenarios.py'}::{scenario}",
        ],
        env=microsoft_environment,
        cwd=tmp_path,
        check=True,
        timeout=45,
    )


@pytest.mark.parametrize(
    "mode", ["disabled", "sticky", "preexisting", "unrelated", "reinit"]
)
def test_native_configuration_lifecycle(mode, microsoft_environment, tmp_path):
    subprocess.run(
        [sys.executable, str(HERE / "agent_framework_lifecycle.py"), mode],
        env=microsoft_environment,
        cwd=tmp_path,
        check=True,
        timeout=30,
    )


def test_environment_excludes_external_telemetry_settings(monkeypatch):
    unwanted = {
        "OTEL_SDK_DISABLED": "true",
        "ENABLE_INSTRUMENTATION": "true",
        "ENABLE_SENSITIVE_DATA": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.invalid",
        "APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=offline",
        "CONFIDENT_API_KEY": "offline",
    }
    for key, value in unwanted.items():
        monkeypatch.setenv(key, value)
    env = scenario_environment()
    for key in unwanted:
        if key.startswith("ENABLE_"):
            assert env[key] == "false"
        else:
            assert key not in env
