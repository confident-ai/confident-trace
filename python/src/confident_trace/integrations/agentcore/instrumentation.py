"""Use upstream ASGI instrumentation for an otherwise uninstrumented runtime."""

import inspect

from opentelemetry import trace
from opentelemetry.trace import SpanKind

from ... import _attributes as confident
from ..._core.runtime import disabled, log
from .._shared.patching import install_targets


def instrument(runtime):
    try:
        from bedrock_agentcore.runtime.app import BedrockAgentCoreApp
        from bedrock_agentcore.runtime.models import SESSION_HEADER
        from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
    except ImportError:
        return []

    if (
        not isinstance(SESSION_HEADER, str)
        or not inspect.iscoroutinefunction(
            getattr(BedrockAgentCoreApp, "__call__", None)
        )
        or not inspect.iscoroutinefunction(
            getattr(OpenTelemetryMiddleware, "__call__", None)
        )
    ):
        return []

    session_header = SESSION_HEADER.lower().encode("ascii")

    def request_hook(span, scope):
        span.set_attribute(
            confident.SPAN_INTEGRATION, confident.Integration.AGENTCORE.value
        )
        # Only attach the documented session identifier, never arbitrary headers.
        for name, value in scope.get("headers", ()):
            if name.lower() == session_header:
                span.set_attribute(confident.TRACE_THREAD_ID, value.decode("latin-1"))
                break

    # Validate constructor capabilities before patching. Some older middleware
    # lacks exclude_spans; that optional noise-control setting is not required.
    options = dict(tracer_provider=runtime.provider, server_request_hook=request_hook)
    try:
        signature = inspect.signature(OpenTelemetryMiddleware)
        if "exclude_spans" in signature.parameters:
            options["exclude_spans"] = ["receive", "send"]
        signature.bind(None, **options)
    except (TypeError, ValueError):
        log.debug("AgentCore ASGI middleware capabilities unavailable")
        return []

    def factory(original, method):
        async def call(wrapped, instance, args, kwargs):
            scope = args[0] if args else kwargs.get("scope", {})
            current = trace.get_current_span()
            if (
                runtime.active
                and not disabled()
                and scope.get("type") == "http"
                and scope.get("path") == "/invocations"
                and getattr(current, "kind", None) == SpanKind.SERVER
            ):
                current.set_attribute(
                    confident.SPAN_INTEGRATION, confident.Integration.AGENTCORE.value
                )
            if (
                not runtime.active
                or disabled()
                or scope.get("type") != "http"
                or scope.get("path") != "/invocations"
                or getattr(current, "kind", None) == SpanKind.SERVER
                or any(
                    isinstance(getattr(item, "cls", None), type)
                    and issubclass(item.cls, OpenTelemetryMiddleware)
                    for item in (getattr(instance, "user_middleware", None) or ())
                )
            ):
                return await wrapped(*args, **kwargs)
            # Cache on the application, not in a global map retaining applications.
            # A later init replaces the cache; old middleware is never called after
            # shutdown. The bound callable/instance cycle is garbage collectable.
            cache = getattr(instance, "_confident_trace_asgi", None)
            if cache is None or cache[0] is not runtime:
                try:
                    middleware = OpenTelemetryMiddleware(wrapped, **options)
                except Exception:
                    # Setup failed before calling the application: safe to bypass.
                    # Never retry an invocation after the middleware starts it.
                    log.debug("AgentCore ASGI middleware setup unavailable")
                    middleware = None
                instance._confident_trace_asgi = (runtime, middleware)
            middleware = instance._confident_trace_asgi[1]
            if middleware is None:
                return await wrapped(*args, **kwargs)
            return await middleware(*args, **kwargs)

        return call

    return install_targets(
        [("bedrock_agentcore.runtime.app", "BedrockAgentCoreApp", "__call__")],
        factory,
    )
