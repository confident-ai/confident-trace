"""Native Pydantic AI telemetry interoperability."""

from .._shared.lifecycle import register_native_inference
from ._constants import SCOPE_NAME


def instrument(runtime):
    from pydantic_ai import Agent

    # There is no public getter for the process default. Preserve configured
    # settings; unknown future layouts leave enablement to the application.
    if getattr(Agent, "_instrument_default", None) is False:
        Agent.instrument_all(True)
    # Native enablement remains in place for other application exporters.
    return register_native_inference(SCOPE_NAME)
