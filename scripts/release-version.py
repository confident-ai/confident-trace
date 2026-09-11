"""Choose an unpublished PyPI version; stdout is reserved for the result."""
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from packaging.version import Version


def next_version(current):
    base = (str(current.epoch) + "!" if current.epoch else "") + current.base_version.split("!")[-1]
    if current.dev is not None:
        return Version(str(current).split(".dev")[0] + f".dev{current.dev + 1}")
    if current.post is not None:
        return Version(str(current).split(".post")[0] + f".post{current.post + 1}")
    if current.pre is not None:
        return Version(base + current.pre[0] + str(current.pre[1] + 1))
    parts = list(current.release)
    while len(parts) < 3:
        parts.append(0)
    parts[-1] += 1
    return Version((str(current.epoch) + "!" if current.epoch else "") + ".".join(map(str, parts)))


def main():
    text = Path("python/pyproject.toml").read_text()
    current = Version(re.search(r'^version = "([^"]+)"$', text, re.M)[1])
    try:
        with urlopen("https://pypi.org/pypi/confident-trace/json", timeout=30) as response:
            published = {Version(v) for v in json.load(response)["releases"]}
    except HTTPError as error:
        if error.code != 404:
            raise
        published = set()
    base = max({current, *published})
    suggested = next_version(base)
    while suggested in published:
        suggested = next_version(suggested)
    selected = sys.argv[1] if len(sys.argv) > 1 else ""
    if not selected:
        print(f"Local: {current}; latest published: {max(published) if published else 'none'}", file=sys.stderr)
        print(f"Next version [{suggested}]: press Enter to continue or enter your own: ", end="", file=sys.stderr, flush=True)
        answer = sys.stdin.readline()
        if not answer:
            raise ValueError("No input received; cancelled. Pass VERSION for noninteractive use.")
        selected = answer.strip() or str(suggested)
    version = Version(selected)
    if version.local:
        raise ValueError("PyPI releases cannot use local version identifiers.")
    if version < current:
        raise ValueError(f"Version {version} is older than local version {current}.")
    if version in published:
        raise ValueError(f"Version {version} is already published. Choose a new version.")
    print(version)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        sys.exit(f"Cannot select release version: {error}")
