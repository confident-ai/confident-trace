#!/usr/bin/env python3
"""Run one CI row locally or in Actions; all versions stay inside this process."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from suites import MINIMUM, PYTHON, REQUIRED, TYPESCRIPT, combinations

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {
    ".git",
    "node_modules",
    ".pnpm-store",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "ci-results",
}


def run(command, cwd, env, log, capture=False):
    print("+ " + " ".join(map(str, command)), file=log, flush=True)
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE if capture else log,
        stderr=log,
        text=True,
        timeout=1200,
    )
    if capture:
        log.write(result.stdout)
        log.flush()
    if result.returncode:
        raise RuntimeError(f"Command exited {result.returncode}: {command}")
    return result.stdout if capture else None


def copy_repository(destination):
    shutil.copytree(
        ROOT,
        destination,
        ignore=lambda _, names: [
            n for n in names if n in EXCLUDED or n.startswith(".venv")
        ],
    )


def python_case(suite, runtime, profile, deps, work, report, env, log):
    repo = work / "repo"
    copy_repository(repo)
    interpreter = run(["uv", "python", "find", runtime], repo, env, log, True).strip()
    run([interpreter, "-m", "venv", str(work / "venv")], repo, env, log)
    python = str(
        work / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    pip = [python, "-m", "pip"]
    install = pip + ["install"]
    if profile == "base":
        run(install + ["-e", "./python[test]", "build", "ruff"], repo, env, log)
        if deps == "minimum":
            run(install + MINIMUM, repo, env, log)
    elif profile == "mega":
        run(
            install
            + ["-e", "./python[test]", "-r", "python/examples/mega/requirements.txt"],
            repo,
            env,
            log,
        )
    else:
        options = (
            ["-c", f"python/tests/constraints/{profile}.txt"]
            if deps == "tested"
            else ["--upgrade"]
        )
        run(
            install + options + ["-e", f"./python[test,{profile}-test]"], repo, env, log
        )
    # Record the actual resolution, including latest's moving transitive deps.
    (report / "dependencies.txt").write_text(
        run(pip + ["freeze"], repo, env, log, True)
    )
    if profile != "base":
        run(pip + ["check"], repo, env, log)
    if suite == "quality":
        run([python, "-m", "ruff", "check", "python", "tools"], repo, env, log)
        run(
            [python, "-m", "ruff", "format", "--check", "python", "tools"],
            repo,
            env,
            log,
        )
        if (repo / "LICENSE").read_bytes() != (repo / "python/LICENSE").read_bytes():
            raise RuntimeError("Python LICENSE differs from root LICENSE")
        run([python, "-m", "build", "python"], repo, env, log)
        return
    required = list(REQUIRED.get(suite, []))
    if suite == "langchain" and runtime != "3.10":
        required.append("deepagents")
    if profile == "mega":
        required = ["claude-agent-sdk", "langchain-openai", "pydantic-ai-slim"]
    if required:
        run(
            [
                python,
                "-c",
                "from importlib.metadata import version; "
                + f"[version(name) for name in {required!r}]",
            ],
            repo,
            env,
            log,
        )
    test_env = env | {
        "CI_SUITE": suite,
        "CI_PROFILE": profile,
        "CI_INVENTORY": str(report / "inventory.json"),
        "PYTHONPATH": str(repo / "scripts/ci") + os.pathsep + env.get("PYTHONPATH", ""),
    }
    tests = (
        ["python/tests"]
        if profile != "mega"
        else ["python/tests/integrations/agent_sdks", "-k", "mega_native"]
    )
    run(
        [
            python,
            "-m",
            "pytest",
            *tests,
            "-p",
            "collection_plugin",
            f"--junitxml={report / 'junit.xml'}",
            "-ra",
        ],
        repo,
        test_env,
        log,
    )
    validate_python_results(report)
    if profile == "mega":
        run(
            [
                python,
                "python/examples/mega/mega.py",
                "--offline",
                "--requests",
                "4",
                "--threads",
                "2",
                "--processes",
                "1",
            ],
            repo,
            env,
            log,
        )


def validate_python_results(report):
    if (
        not (report / "inventory.json").is_file()
        or not (report / "junit.xml").is_file()
    ):
        raise RuntimeError("Missing Python collection inventory or test report")
    inventory = json.loads((report / "inventory.json").read_text())
    if (
        inventory["exitstatus"] != 0
        or not inventory["selected"]
        or inventory["missing"]
        or inventory["unexpected_skips"]
    ):
        raise RuntimeError("Python collection/execution audit failed")


def typescript_case(suite, runtime, profile, deps, work, report, env, log):
    node_dir = env.get(f"NODE_{runtime}")
    if node_dir:
        env = env | {"PATH": node_dir + os.pathsep + env["PATH"]}
    version = run(["node", "--version"], ROOT, env, log, True).strip()
    if version.split(".")[0] != "v" + runtime:
        raise RuntimeError(
            f"Need Node {runtime}; set NODE_{runtime} to its bin directory (found {version})"
        )
    repo = work / "repo"
    copy_repository(repo)
    cwd = repo / "typescript"
    run(["pnpm", "install", "--frozen-lockfile"], cwd, env, log)
    if deps == "minimum":
        # Same policy as test-minimum.mjs: minimum OTel, locked dev versions.
        run(
            ["node", "scripts/test-minimum.mjs", "--prepare-only"],
            cwd,
            env | {"CI_MINIMUM_DESTINATION": str(work / "minimum")},
            log,
        )
        cwd = work / "minimum/typescript"
    run(["node", "scripts/ci-suite.mjs", suite, str(report)], cwd, env, log)


def execute_cases(rows, execute, output):
    """Always attempt the next combination and persist partial failure results."""
    results = []
    output.mkdir(parents=True, exist_ok=True)
    for runtime, profile, deps in rows:
        name = f"{runtime}-{profile}-{deps}"
        report = output / name
        if report.exists():
            shutil.rmtree(report)
        report.mkdir(parents=True)
        start = time.monotonic()
        record = {
            "runtime": runtime,
            "profile": profile,
            "dependencies": deps,
            "log": f"{name}/run.log",
        }
        print(f"::group::{name}", flush=True)
        try:
            with (report / "run.log").open("w") as log:
                execute(runtime, profile, deps, report, log)
            record["result"] = "passed"
        except Exception as error:
            record.update(result="failed", error=str(error))
            with (report / "run.log").open("a") as log:
                print(f"\nERROR: {error}", file=log)
        record["seconds"] = round(time.monotonic() - start, 1)
        results.append(record)
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        # Logs stay in artifacts; show a useful tail without flooding Actions.
        print(
            "\n".join((report / "run.log").read_text().splitlines()[-25:]), flush=True
        )
        print(
            f"{name}: {record['result']} ({record['seconds']}s)\n::endgroup::",
            flush=True,
        )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("language", choices=["python", "typescript"])
    parser.add_argument("suite")
    parser.add_argument("--runtime")
    parser.add_argument("--dependencies")
    parser.add_argument("--profile")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    suites = PYTHON if args.language == "python" else TYPESCRIPT
    if args.suite not in suites:
        parser.error("Choose a suite: " + ", ".join(suites))
    rows = [
        row
        for row in combinations(args.language, args.suite)
        if (not args.runtime or row[0] == args.runtime)
        and (not args.profile or row[1] == args.profile)
        and (not args.dependencies or row[2] == args.dependencies)
    ]
    if not rows:
        parser.error("No combinations match these filters")
    if args.dry_run:
        print(json.dumps(rows, indent=2))
        return 0
    output = ROOT / "ci-results" / args.language / args.suite

    def execute(runtime, profile, deps, report, log):
        with tempfile.TemporaryDirectory(prefix="confident-ci-") as temporary:
            function = python_case if args.language == "python" else typescript_case
            function(
                args.suite,
                runtime,
                profile,
                deps,
                Path(temporary),
                report,
                dict(os.environ),
                log,
            )

    results = execute_cases(rows, execute, output)
    summary = f"## {args.language} / {suites[args.suite]}\n\n"
    summary += "| Runtime | Environment | Dependencies | Result | Seconds | Log in artifact |\n|---|---|---|---|---|---|\n"
    for r in results:
        summary += f"| {r['runtime']} | {r['profile']} | {r['dependencies']} | {r['result']} | {r['seconds']} | `{r['log']}` |\n"
    (output / "summary.md").write_text(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as file:
            file.write(summary)
    return int(
        len(results) != len(rows) or any(r["result"] != "passed" for r in results)
    )


if __name__ == "__main__":
    sys.exit(main())
