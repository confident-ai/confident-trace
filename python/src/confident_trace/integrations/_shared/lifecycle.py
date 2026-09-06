"""Shared call lifecycle, independent of SDK names or payload formats."""

from opentelemetry import context
from opentelemetry.trace import SpanKind

from ..._core import runtime as _runtime
from ..._core.safety import safe
from ..._core.spans import Operation
from ..._semconv import genai_v1_37_0 as ai
from .streams import AsyncStream, Stream

_SUPPRESS = context.create_key("confident_trace.provider_call")


def begin_call(
    operation, fallback_provider, model, params, instance, connection, request
):
    model_name = model if type(model) is str else "unknown"
    attrs = {
        ai.GEN_AI_OPERATION_NAME: operation,
        **(safe(connection, instance) or {ai.GEN_AI_PROVIDER_NAME: fallback_provider}),
    }
    if type(model) is str:
        attrs[ai.GEN_AI_REQUEST_MODEL] = model
    op = Operation(f"{operation} {model_name}", kind=SpanKind.CLIENT, attributes=attrs)
    op.ctx = context.set_value(_SUPPRESS, True, op.ctx)
    safe(request, op, params)
    return op


def finish_call(
    op,
    value,
    response,
    accumulator,
    stream_class=Stream,
    async_stream_class=AsyncStream,
):
    try:
        if hasattr(value, "__anext__"):
            return async_stream_class(value, operation=op, consume=accumulator())
        if hasattr(value, "__next__"):
            return stream_class(value, operation=op, consume=accumulator())
        safe(response, op, value)
    except Exception:
        pass
    op.end()
    return value


def wrapper(begin, finish, *, asynchronous=False, manager=None):
    def bypass():
        rt = _runtime.current()
        return (
            not rt
            or not rt.active
            or _runtime.disabled()
            or context.get_value(_SUPPRESS)
        )

    if asynchronous:

        async def call(wrapped, instance, args, kwargs):
            if bypass():
                return await wrapped(*args, **kwargs)
            op = begin(kwargs, instance)
            try:
                with op.active():
                    result = await wrapped(*args, **kwargs)
                return finish(op, result)
            except BaseException as error:
                op.end(error)
                raise

        return call

    def call(wrapped, instance, args, kwargs):
        if bypass():
            return wrapped(*args, **kwargs)
        if manager:
            return manager(wrapped(*args, **kwargs), kwargs, instance)
        op = begin(kwargs, instance)
        try:
            with op.active():
                result = wrapped(*args, **kwargs)
            return finish(op, result)
        except BaseException as error:
            op.end(error)
            raise

    return call
