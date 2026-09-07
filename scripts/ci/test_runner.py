"""Regression tests for CI orchestration, without installing SDK matrices."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from run import ROOT, execute_cases, validate_python_results
from suites import HOME, PROFILES, PYTHON, TYPESCRIPT, combinations, python_owner


class GroupingTests(unittest.TestCase):
    def test_workflows_have_exactly_29_stable_names(self):
        import yaml

        shared = yaml.safe_load((ROOT / ".github/workflows/shared.yml").read_text())
        self.assertEqual(set(shared["jobs"]), {"consistency"})
        self.assertEqual(shared["jobs"]["consistency"]["name"], "Consistency")
        total = len(shared["jobs"])
        for language, suites in [("python", PYTHON), ("typescript", TYPESCRIPT)]:
            workflow = yaml.safe_load(
                (ROOT / f".github/workflows/{language}.yml").read_text()
            )
            job = workflow["jobs"]["suite"]
            matrix = job["strategy"]["matrix"]
            self.assertEqual(set(matrix), {"include"})
            self.assertFalse(job["strategy"]["fail-fast"])
            self.assertEqual(
                {row["suite"]: row["name"] for row in matrix["include"]}, suites
            )
            total += len(matrix["include"])
        self.assertEqual(total, 29)

    def test_all_original_compatibility_pairs_have_one_scheduled_owner(self):
        examples = [
            "core/test_core.py::test_example",
            "contracts/test_contract.py::test_example",
            *[
                f"integrations/{s}/test_provider.py::test_example"
                for s in ("openai", "anthropic", "google_genai", "bedrock")
            ],
            *[
                f"integrations/{s}/test_framework.py::test_example"
                for s in ("google_adk", "agentcore", "microsoft", "langchain", "crewai")
            ],
            "integrations/native_agents/test_native_agents.py::test_native_agents[pydantic_ai-pydantic-ai-slim]",
            "integrations/native_agents/test_native_agents.py::test_native_agents[strands-strands-agents]",
            "integrations/agent_sdks/test_agent_sdks.py::test_agent_sdk[openai_agents_scenarios-packages0]",
            "integrations/agent_sdks/test_agent_sdks.py::test_agent_sdk[claude_agent_scenarios-packages1]",
            "integrations/agent_sdks/test_agent_sdks.py::test_agent_sdk[mega_native_scenarios-packages2]",
            *[
                f"integrations/frameworks/test_frameworks.py::test_framework[{m}-{s}-distribution]"
                for s in ("agno", "llamaindex", "smolagents")
                for m in (
                    "hierarchy",
                    "ownership",
                    "stream",
                    "concurrency",
                    "termination",
                    "lifecycle",
                    "disabled",
                    "content",
                )
            ],
            "integrations/test_native_lifecycle.py::test_native_import_order_and_disabled[False-True]",
        ]
        for runtime, profile, deps in combinations("python", "core"):
            for test in examples:
                owner = python_owner("python/tests/" + test, profile)
                self.assertIn((runtime, profile, deps), combinations("python", owner))
        self.assertEqual(len(combinations("python", "core")), 56)
        self.assertEqual(len(combinations("python", "interop")), 57)
        for suite in HOME:
            self.assertEqual(len(combinations("python", suite)), 8)
        self.assertEqual(len(PROFILES), 7)
        self.assertEqual(len(combinations("typescript", "core")), 4)

    def test_pytest_python_root_relative_node_ids(self):
        self.assertEqual(
            python_owner("tests/core/test_core.py::test_it", "base"), "core"
        )

    def test_unknown_tests_fail_instead_of_disappearing(self):
        with self.assertRaisesRegex(ValueError, "Unassigned"):
            python_owner(
                "python/tests/integrations/new_sdk/test_new.py::test_it", "base"
            )

    def test_install_test_and_missing_report_failures_do_not_stop_later_cases(self):
        attempted = []

        def execute(runtime, profile, deps, report, log):
            attempted.append(runtime)
            if runtime != "4":
                raise RuntimeError(
                    {"1": "install failed", "2": "test failed", "3": "missing report"}[
                        runtime
                    ]
                )
            print("ok", file=log)

        with (
            tempfile.TemporaryDirectory() as temp,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            output = Path(temp)
            results = execute_cases(
                [(str(n), "base", "latest") for n in range(1, 5)], execute, output
            )
            self.assertEqual(attempted, ["1", "2", "3", "4"])
            self.assertEqual(
                [r["result"] for r in results], ["failed", "failed", "failed", "passed"]
            )
            self.assertEqual(json.loads((output / "results.json").read_text()), results)

    def test_missing_and_incomplete_python_reports_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp)
            with self.assertRaisesRegex(RuntimeError, "Missing"):
                validate_python_results(report)
            (report / "junit.xml").write_text("<testsuites/>")
            inventory = {
                "exitstatus": 0,
                "selected": ["test"],
                "missing": ["test"],
                "unexpected_skips": [],
            }
            (report / "inventory.json").write_text(json.dumps(inventory))
            with self.assertRaisesRegex(RuntimeError, "audit failed"):
                validate_python_results(report)

    def run_plugin(self, source, suite="openai", collect=False):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            testdir = path / (
                "python/tests/integrations/langchain"
                if suite == "langchain"
                else "python/tests/integrations/openai"
            )
            testdir.mkdir(parents=True)
            (testdir / "test_sample.py").write_text(source)
            report = path / "inventory.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "collection_plugin",
                    *(["--collect-only"] if collect else []),
                    "python/tests",
                ],
                cwd=path,
                capture_output=True,
                text=True,
                env=os.environ
                | {
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                    "PYTHONPATH": str(ROOT / "scripts/ci"),
                    "CI_SUITE": suite,
                    "CI_PROFILE": "langchain" if suite == "langchain" else "base",
                    "CI_INVENTORY": str(report),
                },
            )
            return result, json.loads(report.read_text())

    def test_collection_and_execution_inventory(self):
        result, report = self.run_plugin("def test_ok():\n    assert True\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(report["selected"]), 1)
        self.assertEqual(report["missing"], [])
        self.assertEqual(set(report["collected"].values()), {"openai"})

    def test_missing_required_dependency_skip_fails(self):
        result, report = self.run_plugin(
            'import pytest\ndef test_missing():\n    pytest.skip("LangChain is optional")\n',
            suite="langchain",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(report["unexpected_skips"]), 1)

    def test_empty_selection_fails(self):
        result, _ = self.run_plugin("def test_ok():\n    pass\n", suite="core")
        self.assertNotEqual(result.returncode, 0)

    def test_expected_failures_remain_expected(self):
        result, report = self.run_plugin(
            'import pytest\n@pytest.mark.xfail(reason="upstream")\ndef test_expected():\n    assert False\n'
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(any(r["expected_failure"] for r in report["reports"]))


if __name__ == "__main__":
    unittest.main()
