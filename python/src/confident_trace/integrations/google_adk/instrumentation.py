"""ADK owns its spans; only suppress our overlapping provider instrumentation."""

import os
from importlib.metadata import PackageNotFoundError, distribution

from ..._attributes import Integration
from .._shared.lifecycle import register_native_inference, use_global_provider
from ._constants import (
    CAPTURE_MESSAGE_CONTENT,
    LEGACY_CONTENT_IN_SPANS,
    SCOPE_NAME,
    SPAN_ONLY,
)


def _enable_span_content(runtime):
    """Put ADK's IO on its spans when content capture is on and the app is silent.

    ADK reads the knob per call, so setting it here covers runs that start after
    `init()`. Any value the application set, including an empty one, is theirs.
    """
    policy = getattr(runtime, "policy", None)
    if policy is None or not policy.enabled:
        return []
    if CAPTURE_MESSAGE_CONTENT in os.environ:
        return []
    if os.environ.get(LEGACY_CONTENT_IN_SPANS, "").strip().lower() in {"0", "false"}:
        return []
    os.environ[CAPTURE_MESSAGE_CONTENT] = SPAN_ONLY

    def restore():
        if os.environ.get(CAPTURE_MESSAGE_CONTENT) == SPAN_ONLY:
            del os.environ[CAPTURE_MESSAGE_CONTENT]

    return [restore]


def instrument(runtime):
    try:
        distribution("google-adk")
    except PackageNotFoundError:
        return []
    reaches_us = use_global_provider(runtime)
    # Recognition depends on the active span's scope and operation, not the
    # installed SDK version. Unrecognized native spans still export unchanged.
    runtime.processor.integration_scopes[SCOPE_NAME] = Integration.GOOGLE_ADK
    undo = register_native_inference(SCOPE_NAME)
    # Don't turn content on for spans that only reach the application's pipeline.
    if reaches_us:
        undo += _enable_span_content(runtime)
    return undo
