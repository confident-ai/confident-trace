"""Instrument the native litellm SDK using its OpenAI-compatible payloads."""

import inspect

from opentelemetry.trace import StatusCode

from ..._attributes import SPAN_TYPE, Integration
from ..._semconv import genai_v1_37_0 as ai
from .._shared.extraction import get
from .._shared.lifecycle import begin_call, finish_call, wrapper
from .._shared.openai_extraction import request
from .._shared.openai_extraction import response as extract_response
from .._shared.openai_streaming import Accumulator as OpenAIAccumulator
from .._shared.patching import install_targets
from .._shared.streams import AsyncStream, Stream

TARGETS = [
    ("litellm", None, "completion"),
    ("litellm", None, "acompletion"),
    ("litellm", "Router", "completion"),
    ("litellm", "Router", "acompletion"),
]


def begin(params, instance=None):
    return begin_call(
        "chat",
        "litellm",
        params.get("model"),
        params,
        instance,
        lambda _: {ai.GEN_AI_PROVIDER_NAME: "litellm", SPAN_TYPE: "llm"},
        request,
        integration=Integration.LITELLM,
    )


def response(op, value):
    if get(value, "error"):
        op.span.set_status(StatusCode.ERROR)
    extract_response(op, value)


class Accumulator(OpenAIAccumulator):
    def __call__(self, op, chunk):
        if get(chunk, "error"):
            op.span.set_status(StatusCode.ERROR)
        super().__call__(op, chunk)


def finish(op, value, asynchronous=False):
    # LiteLLM exposes both protocols; follow the calling API.
    if asynchronous and hasattr(value, "__anext__"):
        return AsyncStream(value, operation=op, consume=Accumulator())
    if hasattr(value, "__next__"):
        return Stream(value, operation=op, consume=Accumulator())
    return finish_call(op, value, response, Accumulator)


def instrument(runtime):
    def factory(original, method):
        asynchronous = method in (
            "acompletion",
            "send_async",
        ) or inspect.iscoroutinefunction(inspect.unwrap(original))
        return wrapper(
            begin,
            lambda op, value: finish(op, value, asynchronous),
            asynchronous=asynchronous,
            positional=("model", "messages"),
        )

    return install_targets(TARGETS, factory)
