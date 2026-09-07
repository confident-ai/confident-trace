"""Real SDK runs isolated from collection and other frameworks' global hooks."""

import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "framework,distribution",
    [
        ("llamaindex", "llama-index-core"),
        ("agno", "agno"),
        ("smolagents", "smolagents"),
    ],
)
@pytest.mark.parametrize(
    "mode",
    [
        "hierarchy",
        "ownership",
        "stream",
        "concurrency",
        "termination",
        "lifecycle",
        "disabled",
        "content",
    ],
)
def test_framework(framework, distribution, mode, tmp_path):
    try:
        importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(f"{distribution} test extra not installed")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(
            (
                "OTEL_",
                "CONFIDENT_",
                "OPENAI_",
                "AGNO_",
                "HF_",
                "LANGCHAIN_",
                "LANGSMITH_",
                "LLAMA_",
            )
        )
    }
    env.update(
        AGNO_TELEMETRY="false",
        HF_HUB_OFFLINE="1",
        DO_NOT_TRACK="1",
        PYTHONPATH=os.pathsep.join(
            (
                str(Path(__file__).resolve().parents[3] / "src"),
                str(Path(__file__).resolve().parents[2]),
            )
        ),
    )
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name(f"{framework}_scenario.py")),
            mode,
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
