"""Run LiveKit checks without importing or configuring it in pytest's process."""

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from conftest import ROOT

HERE = Path(__file__).parent


@pytest.fixture
def livekit_environment():
    for package in ("livekit-agents", "livekit-plugins-openai"):
        try:
            version(package)
        except PackageNotFoundError:
            pytest.skip(f"{package} is not installed")
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
    env.update(PYTHONPATH=str(ROOT / "python/src"), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return env


@pytest.mark.parametrize(
    "scenario",
    [
        "test_session_spans_are_labelled_and_llm_calls_appear_once",
        "test_unconfigured_livekit_tracer_uses_our_provider",
        "test_configured_livekit_tracer_is_preserved",
        "test_unselected_livekit_keeps_provider_spans",
        "test_privacy_import_order[False]",
        "test_privacy_import_order[True]",
        "test_cleanup_flush_preserves_exception",
        "test_cleanup_flush_is_bounded",
        "test_flush_failure_does_not_replace_cleanup_error",
        "test_call_recording_is_uploaded",
        "test_call_recording_is_skipped[not_recorded]",
        "test_call_recording_is_skipped[content_off]",
        "test_call_recording_is_skipped[pii_off]",
        "test_call_recording_upload_is_bounded",
        "test_session_start_outside_a_job_registers_nothing",
    ],
)
def test_livekit_scenario(scenario, livekit_environment, tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "pytest_asyncio.plugin",
            f"{HERE / 'livekit_scenarios.py'}::{scenario}",
        ],
        env=livekit_environment,
        cwd=tmp_path,
        check=True,
        timeout=60,
    )


@pytest.mark.parametrize("method", ["spawn", "forkserver"])
def test_worker_exit(method, livekit_environment, tmp_path):
    import multiprocessing

    if method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"{method} is unavailable")
    subprocess.run(
        [
            sys.executable,
            str(HERE / "worker_exit.py"),
            method,
            str(tmp_path / "spans.json"),
        ],
        env=livekit_environment,
        cwd=tmp_path,
        check=True,
        timeout=60,
    )
