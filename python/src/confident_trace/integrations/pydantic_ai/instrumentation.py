"""Native Pydantic AI telemetry interoperability."""

from ..._attributes import Integration
from .._shared.lifecycle import register_native_inference, use_global_provider
from ._constants import SCOPE_NAME


def instrument(runtime):
    from pydantic_ai import Agent

    use_global_provider(runtime)

    # There is no public getter for the process default. Preserve configured
    # settings; unknown future layouts leave enablement to the application.
    if getattr(Agent, "_instrument_default", None) is False:
        Agent.instrument_all(True)
    # Native enablement remains in place for other application exporters.
    runtime.processor.integration_scopes[SCOPE_NAME] = Integration.PYDANTIC_AI
    return register_native_inference(SCOPE_NAME)
