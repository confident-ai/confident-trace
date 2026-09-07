"""Run agent SDK configuration and framework behavior in isolated processes."""

import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from conftest import ROOT


@pytest.mark.parametrize(
    "scenario,packages",
    [
        (
            "openai_agents_scenarios",
            ("openai-agents", "openinference-instrumentation-openai-agents"),
        ),
        ("claude_agent_scenarios", ("claude-agent-sdk",)),
        (
            "mega_native_scenarios",
            ("claude-agent-sdk", "langchain-openai", "pydantic-ai-slim"),
        ),
    ],
)
def test_agent_sdk(scenario, packages, tmp_path):
    for package in packages:
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
        CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK="1",
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "pytest_asyncio.plugin",
            str(Path(__file__).with_name(scenario + ".py")),
        ],
        env=env,
        cwd=tmp_path,
        check=True,
        timeout=90,
    )
