"""Enable the optional OpenInference bridge without replacing app processors."""

from importlib.metadata import distribution

from ..._semconv import native
from .._shared.lifecycle import register_native_inference
from ._constants import SCOPE_NAME


def instrument(runtime):
    distribution("openai-agents")
    from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

    bridge = OpenAIAgentsInstrumentor()
    if not bridge.is_instrumented_by_opentelemetry:
        bridge.instrument(tracer_provider=runtime.provider, exclusive_processor=False)
    if not bridge.is_instrumented_by_opentelemetry:
        return []
    # The upstream bridge owns process-wide hooks and its original provider.
    # Keep them for application exporters, as with other native integrations.
    return register_native_inference(
        SCOPE_NAME,
        attribute=native.OPENINFERENCE_KIND,
        values={native.OPENINFERENCE_LLM},
    )
