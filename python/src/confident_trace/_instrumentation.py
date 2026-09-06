"""Small OTel instrumentors for provider surfaces with bounded capture.

No replacement clients; third-party OTel spans pass through untouched.
"""

from __future__ import annotations

import importlib
import inspect

import wrapt
from opentelemetry import context
from opentelemetry.trace import SpanKind

from . import _genai as ai
from . import _runtime
from ._extract import Accumulator, connection, request, response
from ._spans import Operation, safe
from ._streams import AsyncStream, Stream

_SUPPRESS = context.create_key("confident_trace.provider_call")
_TARGETS = {
    "openai": [
        ("openai.resources.chat.completions", c, "create")
        for c in ("Completions", "AsyncCompletions")
    ]
    + [
        ("openai.resources.responses", c, "create")
        for c in ("Responses", "AsyncResponses")
    ],
    "anthropic": [
        ("anthropic.resources.messages", c, method)
        for c in ("Messages", "AsyncMessages")
        for method in ("create", "stream")
    ],
    "google_genai": [
        ("google.genai.models", c, method)
        for c in ("Models", "AsyncModels")
        for method in ("generate_content", "generate_content_stream")
    ],
}


def begin(provider, kwargs, instance=None):
    operation = (
        ai.GEN_AI_OPERATION_NAME__GENERATE_CONTENT
        if provider == "google_genai"
        else ai.GEN_AI_OPERATION_NAME__CHAT
    )
    model = kwargs.get("model")
    model = model if type(model) is str else "unknown"
    op = Operation(
        f"{operation} {model}",
        kind=SpanKind.CLIENT,
        attributes={
            ai.GEN_AI_OPERATION_NAME: operation,
            **(
                safe(connection, provider, instance)
                or {
                    ai.GEN_AI_PROVIDER_NAME: "gcp.gen_ai"
                    if provider == "google_genai"
                    else provider
                }
            ),
            **(
                {ai.GEN_AI_REQUEST_MODEL: kwargs["model"]}
                if type(kwargs.get("model")) is str
                else {}
            ),
        },
    )
    op.ctx = context.set_value(_SUPPRESS, True, op.ctx)
    safe(request, op, provider, kwargs)
    return op


def finish(op, value, provider):
    try:
        return _finish(op, value, provider)
    except Exception:
        op.end()
        return value


def _finish(op, value, provider):
    if hasattr(value, "__anext__"):
        return AsyncStream(value, operation=op, consume=Accumulator(provider))
    if hasattr(value, "__next__"):
        return Stream(value, operation=op, consume=Accumulator(provider))
    safe(response, op, value, provider)
    op.end()
    return value


class Manager(wrapt.ObjectProxy):
    """Anthropic's lazy stream manager starts the request on entry."""

    def __init__(self, wrapped, provider, kwargs, instance):
        super().__init__(wrapped)
        self._self_provider = provider
        self._self_kwargs = kwargs
        self._self_instance = instance
        self._self_op = None

    def __enter__(self):
        op = begin(self._self_provider, self._self_kwargs, self._self_instance)
        self._self_op = op
        try:
            with op.active():
                value = self.__wrapped__.__enter__()
            return Stream(value, operation=op, consume=Accumulator(self._self_provider))
        except BaseException as error:
            op.end(error)
            raise

    def __exit__(self, *args):
        try:
            with self._self_op.active():
                return self.__wrapped__.__exit__(*args)
        finally:
            self._self_op.end(args[1])

    async def __aenter__(self):
        op = begin(self._self_provider, self._self_kwargs, self._self_instance)
        self._self_op = op
        try:
            with op.active():
                value = await self.__wrapped__.__aenter__()
            return AsyncStream(
                value, operation=op, consume=Accumulator(self._self_provider)
            )
        except BaseException as error:
            op.end(error)
            raise

    async def __aexit__(self, *args):
        try:
            with self._self_op.active():
                return await self.__wrapped__.__aexit__(*args)
        finally:
            self._self_op.end(args[1])


def wrapper(provider, asynchronous=False, manager=False):
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
            op = begin(provider, kwargs, instance)
            try:
                with op.active():
                    result = await wrapped(*args, **kwargs)
                return finish(op, result, provider)
            except BaseException as error:
                op.end(error)
                raise

        return call

    def call(wrapped, instance, args, kwargs):
        if bypass():
            return wrapped(*args, **kwargs)
        if manager:
            return Manager(wrapped(*args, **kwargs), provider, kwargs, instance)
        op = begin(provider, kwargs, instance)
        try:
            with op.active():
                result = wrapped(*args, **kwargs)
            return finish(op, result, provider)
        except BaseException as error:
            op.end(error)
            raise

    return call


def instrument(names):
    undo = []
    for name in names:
        for module, class_name, method in _TARGETS.get(name, []):
            try:
                cls = getattr(importlib.import_module(module), class_name)
                original = getattr(cls, method)
                # Do not stack on another wrapt-based provider instrumentor.
                if isinstance(
                    original,
                    (
                        wrapt.ObjectProxy,
                        wrapt.FunctionWrapper,
                        wrapt.BoundFunctionWrapper,
                    ),
                ):
                    continue
                patched = wrapt.FunctionWrapper(
                    original,
                    wrapper(
                        name,
                        inspect.iscoroutinefunction(inspect.unwrap(original)),
                        method == "stream",
                    ),
                )
                setattr(cls, method, patched)

                def restore(cls=cls, method=method, original=original, patched=patched):
                    if vars(cls).get(method) is patched:
                        setattr(cls, method, original)

                undo.append(restore)
            except (ImportError, AttributeError):
                continue
            except Exception:
                _runtime.log.debug("Provider instrumentation unavailable")
    return undo
