"""ADK owns its spans; only suppress our overlapping provider instrumentation."""

from importlib.metadata import PackageNotFoundError, distribution

from ..._attributes import Integration
from .._shared.lifecycle import register_native_inference
from ._constants import SCOPE_NAME


def instrument(runtime):
    try:
        distribution("google-adk")
    except PackageNotFoundError:
        return []
    # Recognition depends on the active span's scope and operation, not the
    # installed SDK version. Unrecognized native spans still export unchanged.
    runtime.processor.integration_scopes[SCOPE_NAME] = Integration.GOOGLE_ADK
    return register_native_inference(SCOPE_NAME)
