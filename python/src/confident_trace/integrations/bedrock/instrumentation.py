"""Instrument only Bedrock Converse calls on Botocore's generated clients."""

from urllib.parse import urlsplit

from opentelemetry import context
from opentelemetry.trace import SpanKind

from ..._attributes import Integration
from ..._core import runtime as _runtime
from ..._core.safety import safe
from ..._core.spans import Operation
from ..._semconv import genai_v1_37_0 as ai
from .._shared.patching import install_targets
from .extraction import request, response
from .streaming import EventStream


def begin(instance, params):
    # Import lazily: shared instrumentation imports this module when patching.
    from .._shared.lifecycle import _SUPPRESS

    model = params.get("modelId")
    attrs = {
        ai.GEN_AI_PROVIDER_NAME: ai.GEN_AI_PROVIDER_NAME__AWS_BEDROCK,
        ai.GEN_AI_OPERATION_NAME: ai.GEN_AI_OPERATION_NAME__CHAT,
    }
    if type(model) is str:
        attrs[ai.GEN_AI_REQUEST_MODEL] = model
    endpoint = urlsplit(instance.meta.endpoint_url)
    if endpoint.hostname:
        attrs[ai.SERVER_ADDRESS] = endpoint.hostname
        attrs[ai.SERVER_PORT] = endpoint.port or (
            443 if endpoint.scheme == "https" else 80
        )
    op = Operation(
        f"{ai.GEN_AI_OPERATION_NAME__CHAT} {model if type(model) is str else 'unknown'}",
        kind=SpanKind.CLIENT,
        attributes=attrs,
        integration=Integration.BEDROCK,
    )
    op.ctx = context.set_value(_SUPPRESS, True, op.ctx)
    safe(request, op, params)
    return op


def wrapper(wrapped, instance, args, kwargs):
    from .._shared.lifecycle import _SUPPRESS

    rt = _runtime.current()
    if not rt or not rt.active or _runtime.disabled() or context.get_value(_SUPPRESS):
        return wrapped(*args, **kwargs)
    operation = args[0] if args else kwargs.get("operation_name")
    params = args[1] if len(args) > 1 else kwargs.get("api_params")
    if operation not in ("Converse", "ConverseStream") or type(params) is not dict:
        return wrapped(*args, **kwargs)
    try:
        if instance.meta.service_model.service_name != "bedrock-runtime":
            return wrapped(*args, **kwargs)
    except AttributeError:
        return wrapped(*args, **kwargs)
    op = safe(begin, instance, params)
    if op is None:
        return wrapped(*args, **kwargs)
    try:
        with op.active():
            result = wrapped(*args, **kwargs)
    except BaseException as error:
        op.end(error)
        raise
    try:
        safe(response, op, result)
        if (
            operation == "ConverseStream"
            and type(result) is dict
            and "stream" in result
        ):
            result["stream"] = EventStream(result["stream"], op)
        else:
            op.end()
    except Exception:
        op.end()
    return result


def instrument(runtime):
    return install_targets(
        [("botocore.client", "BaseClient", "_make_api_call")],
        lambda original, method: wrapper,
    )
