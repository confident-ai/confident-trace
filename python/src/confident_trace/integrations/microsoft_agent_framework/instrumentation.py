"""Enable native Agent Framework telemetry on the application's OTel provider."""

from ..._attributes import Integration
from .._shared.lifecycle import register_native_inference
from ._constants import SCOPE_NAME


def instrument(runtime):
    from agent_framework import observability

    settings = observability.OBSERVABILITY_SETTINGS
    # Preserve native content settings and the framework's sticky user disable.
    # Native instrumentation remains application-owned across our shutdown.
    if not settings.enable_instrumentation:
        observability.enable_instrumentation(
            enable_sensitive_data=settings.enable_sensitive_data
        )
    runtime.processor.integration_scopes[SCOPE_NAME] = Integration.MICROSOFT_AGENT_FRAMEWORK
    return register_native_inference(SCOPE_NAME)
