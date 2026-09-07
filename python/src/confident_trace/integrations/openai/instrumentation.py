"""Install openai instrumentation without importing its SDK until selected."""

import inspect

from ..._semconv import genai_v1_37_0 as ai
from .._shared.lifecycle import begin_call, finish_call, wrapper
from .._shared.patching import install_targets
from .extraction import connection, request, response
from .streaming import Accumulator

TARGETS = [
    ("openai.resources.chat.completions", c, "create")
    for c in ("Completions", "AsyncCompletions")
] + [
    ("openai.resources.responses", c, "create") for c in ("Responses", "AsyncResponses")
]


def begin(params, instance=None):
    return begin_call(
        ai.GEN_AI_OPERATION_NAME__CHAT,
        ai.GEN_AI_PROVIDER_NAME__OPENAI,
        params.get("model"),
        params,
        instance,
        connection,
        request,
    )


def finish(op, value):
    return finish_call(op, value, response, Accumulator)


def instrument(runtime):
    return install_targets(
        TARGETS,
        lambda original, method: wrapper(
            begin,
            finish,
            asynchronous=inspect.iscoroutinefunction(inspect.unwrap(original)),
            manager=None,
        ),
    )
