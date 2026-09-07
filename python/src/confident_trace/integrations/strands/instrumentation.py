"""Native Strands telemetry interoperability."""

from importlib.metadata import version

from ..._attributes import Integration
from .._shared.lifecycle import register_native_inference
from ._constants import SCOPE_NAME


def instrument(runtime):
    version("strands-agents")
    runtime.processor.integration_scopes[SCOPE_NAME] = Integration.STRANDS
    return register_native_inference(SCOPE_NAME)
