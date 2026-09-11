#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [VERSION] [--build-only]"
  echo "       $0 --save-token"
  echo "Omit VERSION to accept a suggested next version or enter your own."
  echo "Example: $0 0.1.1"
  echo "Creates a release environment, updates both Python versions, builds, validates, and uploads to PyPI."
  echo "--build-only updates versions and validates distributions without uploading."
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [[ "${1:-}" == "--save-token" && $# -eq 1 ]]; then
  python3 - "$root/.pypi-token" <<'PYTOKEN'
import getpass
import os
import sys

# Read from the terminal without echoing or putting the token in shell history.
token = getpass.getpass("PyPI API token: ").strip()
if not token.startswith("pypi-") or any(c.isspace() for c in token):
    raise SystemExit("Expected a PyPI API token starting with pypi-.")
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "w") as handle:
    os.fchmod(handle.fileno(), 0o600)
    handle.write(token + "\n")
print("Saved token to .pypi-token (readable only by your account).")
PYTOKEN
  exit 0
fi

version=""
preview=false
for arg in "$@"; do
  case "$arg" in
    --build-only) preview=true ;;
    -*) usage >&2; exit 1 ;;
    *) if [[ -n "$version" ]]; then usage >&2; exit 1; fi; version="$arg" ;;
  esac
done

release_python="$root/.venv-release/bin/python"
if [[ ! -x "$release_python" ]]; then
  python3 -m venv "$root/.venv-release"
fi
"$release_python" -m pip install --upgrade pip build hatchling twine packaging

version="$("$release_python" "$root/scripts/release-version.py" "$version")"

"$release_python" - "$version" <<'PY'
import re
import sys
from pathlib import Path

from packaging.version import Version

version = str(Version(sys.argv[1]))
updates = []
for filename, key in (
    ("python/pyproject.toml", "version"),
    ("python/src/confident_trace/_core/runtime.py", "VERSION"),
):
    path = Path(filename)
    content = path.read_text()
    pattern = rf'^{key} = "([^"]+)"$'
    matches = re.findall(pattern, content, flags=re.MULTILINE)
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one {key} in {path}")
    if Version(version) < Version(matches[0]):
        raise SystemExit(f"Version {version} is older than {matches[0]} in {path}")
    updated = re.sub(pattern, f'{key} = "{version}"', content, flags=re.MULTILINE)
    updates.append((path, updated))
for path, content in updates:
    path.write_text(content)
print(f"Building confident-trace {version}")
PY

# Clean only package build outputs so old releases cannot be uploaded.
rm -rf "$root/python/dist"
"$release_python" -m build python
"$release_python" -m twine check python/dist/*

if [[ "$preview" == true ]]; then
  echo "Validated distributions are in $root/python/dist (not uploaded)."
else
  # Read the token as plain text, never as shell code. Environment takes precedence.
  if [[ -z "${TWINE_PASSWORD:-}" && -f "$root/.pypi-token" ]]; then
    TWINE_PASSWORD="$(cat "$root/.pypi-token")"
    if [[ -z "$TWINE_PASSWORD" ]]; then
      echo "Token file is empty. Run $0 --save-token." >&2
      exit 1
    fi
    export TWINE_PASSWORD
  fi
  # Twine prompts if neither the environment nor the local file supplies a token.
  TWINE_USERNAME=__token__ "$release_python" -m twine upload \
    --repository-url https://upload.pypi.org/legacy/ python/dist/*
fi
