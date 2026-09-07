"""CI-only collection audit. Normal local pytest behavior remains unchanged."""

import json
import os
from pathlib import Path

import pytest
from suites import HOME, python_owner


def pytest_configure(config):
    config.ci_inventory = {"collected": {}, "selected": [], "reports": []}


def pytest_collection_modifyitems(config, items):
    suite, profile = os.environ["CI_SUITE"], os.environ["CI_PROFILE"]
    selected, deselected = [], []
    for item in items:
        owner = python_owner(item.nodeid, profile)
        config.ci_inventory["collected"][item.nodeid] = owner
        (selected if owner == suite else deselected).append(item)
    config.ci_inventory["selected"] = [item.nodeid for item in selected]
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    result = yield
    report = result.get_result()
    item.config.ci_inventory["reports"].append(
        {
            "nodeid": report.nodeid,
            "when": report.when,
            "outcome": report.outcome,
            "expected_failure": hasattr(report, "wasxfail"),
            "reason": str(report.longrepr) if report.skipped else "",
        }
    )


def pytest_sessionfinish(session, exitstatus):
    inventory = session.config.ci_inventory
    suite = os.environ["CI_SUITE"]
    inventory["exitstatus"] = int(exitstatus)
    # A required integration must not silently disappear because importorskip
    # happened during collection or because every selected test skipped.
    reports = inventory["reports"]
    unexpected = [
        r
        for r in reports
        if r["outcome"] == "skipped"
        and not r["expected_failure"]
        and suite in HOME
        and any(
            s in r["reason"]
            for s in ("not installed", "not found", "could not import", "is optional")
        )
    ]
    completed = {
        r["nodeid"]
        for r in reports
        if r["when"] == "call" or r["outcome"] in ("failed", "skipped")
    }
    missing = set(inventory["selected"]) - completed
    if not inventory["selected"] or unexpected or missing:
        if not session.config.option.collectonly:
            session.exitstatus = 1
    inventory["missing"] = sorted(missing)
    inventory["unexpected_skips"] = unexpected
    inventory["exitstatus"] = int(session.exitstatus)
    Path(os.environ["CI_INVENTORY"]).write_text(json.dumps(inventory, indent=2) + "\n")
