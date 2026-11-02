"""Run Pipecat checks without importing or configuring it in pytest's process."""

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from conftest import ROOT

HERE = Path(__file__).parent


@pytest.fixture
def pipecat_environment():
    try:
        version("pipecat-ai")
    except PackageNotFoundError:
        pytest.skip("pipecat-ai is not installed")
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
        "test_conversation_spans_are_labelled_and_llm_calls_appear_once",
        "test_explicit_enable_tracing_false_is_respected",
        "test_unselected_pipecat_leaves_tracing_off",
        "test_unselected_pipecat_with_tracing_keeps_provider_spans",
        "test_passed_provider_becomes_global_when_unset",
        "test_configured_global_provider_is_preserved",
        "test_cleanup_flushes_after_the_conversation_ends",
        "test_cleanup_flush_is_bounded",
        "test_flush_failure_does_not_break_cleanup",
        "test_shutdown_restores_pipecat",
        "test_capture_content_off_drops_pipecat_content",
        "test_redactor_rewrites_pipecat_content",
        "test_failing_redactor_omits_pipecat_content",
        "test_content_is_kept_by_default",
        "test_call_recording_is_uploaded_to_the_conversation_trace",
        "test_call_recording_is_uploaded_before_the_final_flush",
        "test_call_recording_is_skipped",
        "test_user_audio_handlers_keep_receiving_audio",
        "test_call_recording_upload_is_bounded",
        "test_buffered_audio_is_uploaded_as_one_recording",
        "test_start_body_links_the_trace_to_its_test_case",
    ],
)
def test_pipecat_scenario(scenario, pipecat_environment, tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "pytest_asyncio.plugin",
            f"{HERE / 'pipecat_scenarios.py'}::{scenario}",
        ],
        env=pipecat_environment,
        cwd=tmp_path,
        check=True,
        timeout=60,
    )
