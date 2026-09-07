import asyncio
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from confident_trace._core import diagnostics
from confident_trace._core.safety import safe
from confident_trace._core.spans import fields


@pytest.fixture(autouse=True)
def diagnostic_state():
    diagnostics._after_fork()
    yield
    diagnostics._after_fork()


def fail(*args, **kwargs):
    raise ValueError("SECRET credential and prompt")


def test_default_quiet_and_debug_does_not_expose_content(caplog):
    with caplog.at_level(logging.INFO, logger=diagnostics.log.name):
        assert safe(fail, "SECRET argument", token="SECRET token") is None
    assert not caplog.records
    assert diagnostics._count == 0
    with caplog.at_level(logging.DEBUG, logger=diagnostics.log.name):
        assert safe(fail, "SECRET argument", token="SECRET token") is None
        # Genuine SDK callsite metadata is useful; user functions get a generic label.
        assert safe(fields, None, {}) is None
    assert "telemetry failed (ValueError)" in caplog.text
    assert "_core.spans.fields failed (AttributeError)" in caplog.text
    assert "SECRET" not in caplog.text
    assert all(r.exc_info is None and r.stack_info is None for r in caplog.records)


def test_success_and_control_flow_are_unchanged(caplog):
    value = object()
    with caplog.at_level(logging.DEBUG, logger=diagnostics.log.name):
        assert safe(lambda: value) is value
        for error in (asyncio.CancelledError(), KeyboardInterrupt(), SystemExit()):

            def stop():
                raise error

            with pytest.raises(type(error)) as caught:
                safe(stop)
            assert caught.value is error
    assert not caplog.records


def test_rate_limit_is_bounded_concurrently_and_recovers(caplog, monkeypatch):
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: 100.0)
    with caplog.at_level(logging.DEBUG, logger=diagnostics.log.name):
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert list(pool.map(lambda _: safe(fail), range(100))) == [None] * 100
        assert len(caplog.records) == 10
        assert sum("suppressed" in r.getMessage() for r in caplog.records) == 1
        monkeypatch.setattr(diagnostics.time, "monotonic", lambda: 160.0)
        safe(fail)
        assert len(caplog.records) == 11


def test_hostile_callables_and_logging_handlers_stay_fail_open(caplog):
    class Callable:
        def __repr__(self):
            raise AssertionError("Must not inspect user objects")

        def __call__(self):
            raise ValueError("SECRET")

    class Handler(logging.Handler):
        def emit(self, record):
            safe(fail)  # Nested diagnostic must not recurse or deadlock.
            raise RuntimeError("broken application logger")

    handler = Handler()
    diagnostics.log.addHandler(handler)
    try:
        with caplog.at_level(logging.DEBUG, logger=diagnostics.log.name):
            assert safe(Callable()) is None
            assert diagnostics._count == 1
    finally:
        diagnostics.log.removeHandler(handler)
    with caplog.at_level(logging.DEBUG, logger=diagnostics.log.name):
        safe(fail)
    assert diagnostics._count == 2
    assert "SECRET" not in caplog.text


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork only")
def test_child_resets_inherited_locked_diagnostics():
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("diagnostics_fork_scenario.py")),
        ],
        check=True,
        timeout=15,
    )
