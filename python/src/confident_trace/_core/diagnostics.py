"""Bounded, content-free diagnostics for SDK-internal failures."""

import logging
import os
import threading
import time
from pathlib import Path
from types import FunctionType, MethodType

from .. import _attributes as confident

log = logging.getLogger(confident.SCOPE_NAME + ".diagnostics")
_PACKAGE = Path(__file__).resolve().parents[1]
_LIMIT = 10
_INTERVAL = 60.0
_lock = threading.Lock()
_local = threading.local()
_window = 0.0
_count = 0


def _after_fork():
    global _lock, _local, _window, _count
    _lock = threading.Lock()
    _local = threading.local()
    _window = 0.0
    _count = 0


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _operation(call):
    # Never inspect/repr arbitrary callable objects or user-supplied names.
    # Only immutable code metadata from functions inside this package is used.
    if isinstance(call, MethodType):
        call = call.__func__
    if not isinstance(call, FunctionType):
        return "telemetry"
    try:
        relative = Path(call.__code__.co_filename).relative_to(_PACKAGE)
    except ValueError:
        return "telemetry"
    return f"{relative.with_suffix('').as_posix().replace('/', '.')}.{call.__code__.co_name}"


def report_failure(call, error):
    """Debug only; logging failures must never escape into application code."""
    global _window, _count
    try:
        if not log.isEnabledFor(logging.DEBUG) or getattr(_local, "reporting", False):
            return
        _local.reporting = True
        try:
            now = time.monotonic()
            with _lock:
                if now - _window >= _INTERVAL:
                    _window, _count = now, 0
                if _count >= _LIMIT:
                    return
                _count += 1
                final = _count == _LIMIT
            # Invoke user-owned logging handlers outside the lock. The guard also
            # prevents handlers that fail through safe() from recursing.
            log.debug(
                "Telemetry operation %s failed (%s)%s",
                _operation(call),
                type(error).__name__,
                "; further diagnostics suppressed until the next window"
                if final
                else "",
            )
        finally:
            _local.reporting = False
    except Exception:
        pass
