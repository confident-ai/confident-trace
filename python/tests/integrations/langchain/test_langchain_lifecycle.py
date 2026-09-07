import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["disabled", "reinit", "later-wrapper", "fork"])
def test_lifecycle(mode):
    if mode == "fork" and not hasattr(os, "fork"):
        pytest.skip("fork requires POSIX")
    if importlib.util.find_spec("langchain_core") is None:
        pytest.skip("LangChain is optional")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("OTEL_", "LANGCHAIN_", "LANGSMITH_", "CONFIDENT_"))
    }
    subprocess.run(
        [sys.executable, str(Path(__file__).with_name("lifecycle_scenario.py")), mode],
        env=env,
        check=True,
        timeout=30,
    )
