"""Isolate native framework globals from collection and other integrations."""

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from conftest import ROOT


@pytest.mark.parametrize(
    "framework,package",
    [("pydantic_ai", "pydantic-ai-slim"), ("strands", "strands-agents")],
)
def test_native_agents(framework, package, tmp_path):
    try:
        version(package)
    except PackageNotFoundError:
        pytest.skip(f"{package} is not installed")
    allowed = {
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "WINDIR",
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
        CT_TEST_FRAMEWORK=framework,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "pytest_asyncio.plugin",
            str(Path(__file__).with_name("native_agent_scenarios.py")),
        ],
        env=env,
        cwd=tmp_path,
        check=True,
        timeout=90,
    )
