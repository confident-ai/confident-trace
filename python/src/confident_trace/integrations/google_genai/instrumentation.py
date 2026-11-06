"""Install google_genai instrumentation without importing its SDK until selected."""

import inspect
import sys

from opentelemetry import trace

from ..._attributes import Integration
from ..._semconv import genai_v1_41_0 as ai
from .._shared.lifecycle import begin_call, finish_call, same_provider, wrapper
from .._shared.patching import install_targets
from ._constants import INSTRUMENTATION_MODULE, SCOPE_NAMES
from .extraction import connection, request, response
from .streaming import Accumulator

TARGETS = [
    ("google.genai.models", c, method)
    for c in ("Models", "AsyncModels")
    for method in ("generate_content", "generate_content_stream")
]


def begin(params, instance=None, *, start_time=None):
    return begin_call(
        ai.GEN_AI_OPERATION_NAME__GENERATE_CONTENT,
        ai.GEN_AI_PROVIDER_NAME__GCP_GEN_AI,
        params.get("model"),
        params,
        instance,
        connection,
        request,
        integration=Integration.GOOGLE_GENAI,
        start_time=start_time,
    )


def finish(op, value):
    return finish_call(op, value, response, Accumulator)


def instrumentor_active(rt):
    """Only an observed span on our provider is proof of ownership."""
    current = trace.get_current_span()
    scope = getattr(current, "instrumentation_scope", None)
    return (
        scope is not None and scope.name in SCOPE_NAMES and same_provider(current, rt)
    )


def begin_observed(params, instance=None):
    # A loaded external instrumentor may wrap inside us. Observe its public span
    # callbacks before deciding whether a fallback span is necessary.
    if INSTRUMENTATION_MODULE in sys.modules:
        from .observation import DeferredOperation

        return DeferredOperation(
            lambda start: begin(params, instance, start_time=start)
        )
    return begin(params, instance)


def instrument(runtime):
    return install_targets(
        TARGETS,
        lambda original, method: wrapper(
            begin_observed,
            finish,
            asynchronous=inspect.iscoroutinefunction(inspect.unwrap(original)),
            manager=None,
            stand_down_when=instrumentor_active,
        ),
        allow_wrapped=True,
    )
