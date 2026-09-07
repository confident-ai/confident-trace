"""Native Strands telemetry interoperability."""

from importlib.metadata import version

from .._shared.lifecycle import register_native_inference
from ._constants import SCOPE_NAME


def instrument(runtime):
    version("strands-agents")
    return register_native_inference(SCOPE_NAME)
