"""Private embedding seam for in-process native-span consumers.

Unlike public init(), this attaches no exporter, changes no global provider,
and enables no framework instrumentation. The embedding SDK owns those choices.
Keep this module private until the consumer contract has shipped and stabilized.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import SpanProcessor

from .._attributes import Integration
from .._semconv.native import NATIVE_INTEGRATION_SCOPES
from .runtime import OwnedProcessor, disabled

_SCOPES = {
    "pydantic_ai": {NATIVE_INTEGRATION_SCOPES["pydantic_ai"]: Integration.PYDANTIC_AI},
    "strands": {NATIVE_INTEGRATION_SCOPES["strands"]: Integration.STRANDS},
    "google_adk": {NATIVE_INTEGRATION_SCOPES["google_adk"]: Integration.GOOGLE_ADK},
    # AgentCore request spans vary by ASGI instrumentation; DeepEval retains
    # that framework-specific translation. Do not relabel arbitrary ASGI spans.
    "agentcore": {},
}


class NativeAttachment(OwnedProcessor):
    def enable(self, integrations):
        for name in integrations:
            if name not in _SCOPES:
                raise ValueError(f"Unsupported native integration: {name}")
        for name in integrations:
            self.integration_scopes.update(_SCOPES[name])

    def on_start(self, span, parent_context=None):
        if self.closed or disabled():
            return
        super().on_start(span, parent_context)
        self.delegate.on_start(span, parent_context)

    def on_end(self, span):
        if not disabled():
            super().on_end(span)


def attach_native(provider, processor: SpanProcessor, *, integrations=()):
    """Attach a synchronous consumer to an explicitly supplied SDK provider.

    The caller retains the returned gate for additional enablement and shutdown.
    It must deduplicate attachment per provider. Shutdown closes only this gate
    and its delegate; it never shuts down the supplied provider.
    """
    gate = NativeAttachment(processor)
    gate.enable(integrations)
    provider.add_span_processor(gate)
    return gate


_FRAMEWORKS = {
    "langchain": Integration.LANGCHAIN,
    "llamaindex": Integration.LLAMAINDEX,
    "crewai": Integration.CREWAI,
    "openai": Integration.OPENAI,
    "anthropic": Integration.ANTHROPIC,
    "openai_agents": Integration.OPENAI_AGENTS,
}


def instrument_framework(rt, name, **options):
    """Enable one privately owned adapter without calling public init().

    The caller supplies the provider and processor and owns teardown. Public
    runtime, exporters and global provider are untouched. One runtime may own
    each integration; conflicting embedding requests fail before patching.
    """
    from importlib import import_module

    from . import runtime

    integration = _FRAMEWORKS[name]
    previous = runtime._embedded.get(integration)
    if previous is not None and previous is not rt and previous.active:
        raise RuntimeError(f"{name} already has an embedding owner")
    runtime._embedded[integration] = rt
    try:
        module = import_module(
            f"..integrations.{name}.instrumentation", package=__package__
        )
        undo = module.instrument(rt, **options)
    except BaseException:
        if previous is None:
            runtime._embedded.pop(integration, None)
        else:
            runtime._embedded[integration] = previous
        raise

    def release():
        if runtime._embedded.get(integration) is rt:
            runtime._embedded.pop(integration, None)

    return [release, *undo]
