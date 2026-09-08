"""Enable the optional OpenInference bridge without replacing app processors."""

from importlib.metadata import distribution

from ..._attributes import Integration
from ..._semconv import native
from .._shared.lifecycle import register_native_inference
from ._constants import SCOPE_NAME


def instrument(runtime, *, explicit_processor=False):
    distribution("openai-agents")
    if explicit_processor:
        runtime.processor.integration_scopes[SCOPE_NAME] = Integration.OPENAI_AGENTS
        return []
    from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

    bridge = OpenAIAgentsInstrumentor()
    if not bridge.is_instrumented_by_opentelemetry:
        bridge.instrument(tracer_provider=runtime.provider, exclusive_processor=False)
    if not bridge.is_instrumented_by_opentelemetry:
        return []
    # The upstream bridge owns process-wide hooks and its original provider.
    # Keep them for application exporters, as with other native integrations.
    runtime.processor.integration_scopes[SCOPE_NAME] = Integration.OPENAI_AGENTS
    return register_native_inference(
        SCOPE_NAME,
        attribute=native.OPENINFERENCE_KIND,
        values={native.OPENINFERENCE_LLM},
    )


def create_processor(runtime):
    """Construct an explicit processor; do not register it with the Agents SDK."""
    from openinference.instrumentation import OITracer, TraceConfig
    from openinference.instrumentation.openai_agents._processor import (
        OpenInferenceTracingProcessor,
    )

    runtime.processor.integration_scopes[SCOPE_NAME] = Integration.OPENAI_AGENTS
    tracer = OITracer(runtime.provider.get_tracer(SCOPE_NAME), config=TraceConfig())
    return OpenInferenceTracingProcessor(tracer)
