"""Each real-framework scenario isolates CrewAI's process-global listeners."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "mode",
    [
        "hierarchy",
        "async",
        "concurrency",
        "structured",
        "flow",
        "stream",
        "termination",
        "lifecycle",
        "disabled",
        "content",
        "workers",
        "stream-close",
        "resume",
        "diagnostics",
    ],
)
def test_crewai(mode, tmp_path):
    spec = importlib.util.find_spec("crewai")
    if spec is None or spec.origin is None:
        pytest.skip("CrewAI test extra not installed")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            ("OTEL_", "CREWAI_", "CONFIDENT_", "OPENAI_", "LANGSMITH_", "LANGCHAIN_")
        )
    }
    env.update(
        CREWAI_DISABLE_TELEMETRY="true",
        CREWAI_TRACING_ENABLED="false",
        CREWAI_STORAGE_DIR=str(tmp_path),
        PYTHONPATH=os.pathsep.join(
            (
                str(Path(__file__).resolve().parents[3] / "src"),
                str(Path(__file__).resolve().parents[2]),
            )
        ),
    )
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("crewai_scenario.py")), mode],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
