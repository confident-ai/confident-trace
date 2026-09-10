"""Shared call lifecycle, independent of SDK names or payload formats."""

from opentelemetry import context, trace
from opentelemetry.trace import SpanKind

from ... import _attributes as confident
from ..._core import runtime as _runtime
from ..._core.safety import safe
from ..._core.spans import Operation
from ..._semconv import genai_v1_37_0 as ai
from ..._semconv import native as native_ai
from .streams import AsyncStream, Stream

_SUPPRESS = context.create_key(confident.PROVIDER_CALL_CONTEXT_KEY)
_NATIVE_INFERENCE_SCOPES = {}


def register_native_inference(
    scope_name,
    *,
    attribute=native_ai.OPERATION_NAME,
    values=native_ai.INFERENCE_OPERATIONS,
):
    """Recognize a verified native inference scope without patching its SDK."""
    key = scope_name
    if key in _NATIVE_INFERENCE_SCOPES:
        return []
    owner = (object(), attribute, frozenset(values))
    _NATIVE_INFERENCE_SCOPES[key] = owner

    def remove():
        if _NATIVE_INFERENCE_SCOPES.get(key) is owner:
            del _NATIVE_INFERENCE_SCOPES[key]

    return [remove]


def native_inference_active(rt):
    if not _NATIVE_INFERENCE_SCOPES:
        return False
    current = trace.get_current_span()
    # OTel exposes no public span-to-provider link. Compare the SDK processor
    # identity conservatively: native settings can select a non-global provider.
    # If a future SDK changes these internals, keep the provider wrapper active.
    processor = getattr(current, "_span_processor", None)
    if processor is None or processor is not getattr(
        rt.provider, "_active_span_processor", None
    ):
        return False
    scope = getattr(current, "instrumentation_scope", None)
    registration = _NATIVE_INFERENCE_SCOPES.get(scope.name) if scope else None
    if registration is None:
        return False
    _, attribute, values = registration
    return (getattr(current, "attributes", None) or {}).get(attribute) in values


def begin_call(
    operation,
    fallback_provider,
    model,
    params,
    instance,
    connection,
    request,
    *,
    integration: confident.Integration,
):
    from .gateways import gateway_name, matches_endpoint

    rt = _runtime.current()
    gateway = None
    if rt and integration == confident.Integration.OPENAI:
        gateway = gateway_name(
            instance,
            rt.litellm_proxy_urls,
            rt.openrouter_proxy_urls,
            rt.portkey_proxy_urls,
            rt.bifrost_proxy_urls,
            rt.truefoundry_proxy_urls,
        )
    elif rt and integration == confident.Integration.ANTHROPIC:
        if matches_endpoint(instance, rt.bifrost_proxy_urls):
            gateway = "bifrost"
        elif matches_endpoint(instance, rt.truefoundry_proxy_urls):
            gateway = "truefoundry"
    model_name = model if type(model) is str else "unknown"
    attrs = {
        ai.GEN_AI_OPERATION_NAME: operation,
        **(safe(connection, instance) or {ai.GEN_AI_PROVIDER_NAME: fallback_provider}),
    }
    if gateway:
        attrs[confident.GATEWAY_NAME] = gateway
        attrs[ai.GEN_AI_PROVIDER_NAME] = gateway
    if type(model) is str:
        attrs[ai.GEN_AI_REQUEST_MODEL] = model
    op = Operation(
        f"{operation} {model_name}",
        kind=SpanKind.CLIENT,
        attributes=attrs,
        integration=integration,
    )
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


def wrapper(begin, finish, *, asynchronous=False, manager=None, positional=()):
    def bypass():
        rt = _runtime.current()
        return (
            not rt
            or not rt.active
            or _runtime.disabled()
            or context.get_value(_SUPPRESS)
            or native_inference_active(rt)
        )

    if asynchronous:

        async def call(wrapped, instance, args, kwargs):
            if bypass():
                return await wrapped(*args, **kwargs)
            op = begin({**dict(zip(positional, args)), **kwargs}, instance)
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
        op = begin({**dict(zip(positional, args)), **kwargs}, instance)
        try:
            with op.active():
                result = wrapped(*args, **kwargs)
            return finish(op, result)
        except BaseException as error:
            op.end(error)
            raise

    return call
