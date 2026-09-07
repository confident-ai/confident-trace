"""Fail-open telemetry calls; application calls must not use this helper."""

from .diagnostics import report_failure


def safe(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except Exception as error:
        report_failure(call, error)
        return None
