"""The same public declarations used by IDEs must reject invalid callers."""

import os
import subprocess
import sys
from pathlib import Path


def test_public_ide_signatures(tmp_path):
    python = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--follow-imports=silent",
            "--cache-dir",
            str(tmp_path / "mypy"),
            str(Path(__file__).with_name("consumer.py")),
        ],
        env={**os.environ, "MYPYPATH": str(python / "src")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
