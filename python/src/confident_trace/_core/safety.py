"""Fail-open telemetry calls; application calls must not use this helper."""


def safe(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except Exception:
        return None
