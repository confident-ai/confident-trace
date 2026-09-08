"""Owned execution scopes for frameworks whose providers own inference spans."""

import inspect
import os
import threading
import weakref

import wrapt
from opentelemetry.trace import Status, StatusCode

from ..._attributes import Integration
from ..._core import runtime
from ..._core.safety import safe
from ..._core.spans import Operation
from ..._semconv import genai_v1_37_0 as ai
from .streams import AsyncStream, Stream

_STATES = weakref.WeakSet()


def _after_fork():
    for state in tuple(_STATES):
        state.after_fork()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


class State:
    def __init__(self, rt, *, integration: Integration):
        self.integration = integration
        self.rt = rt
        self.lock = threading.RLock()
        self.operations = set()
        self.closed = False
        _STATES.add(self)

    def after_fork(self):
        self.lock = threading.RLock()
        self.operations = set()

    def enabled(self):
        return (
            not self.closed
            and self.rt is runtime.for_integration(self.integration)
            and self.rt.active
            and not runtime.disabled()
        )

    def start(self, name, attributes=None):
        op = Execution(self, name, attributes=attributes, integration=self.integration)
        with self.lock:
            closed = self.closed
            if not closed:
                self.operations.add(op)
        if closed:
            Operation.end(op)
        return op

    def close(self):
        with self.lock:
            self.closed = True
            operations = tuple(self.operations)
        for op in operations:
            op.end()


class Execution(Operation):
    def __init__(self, state, *args, **kwargs):
        self.state = state
        self.text = ""
        self.streaming = False
        super().__init__(*args, **kwargs)

    def end(self, error=None):
        with self.state.lock:
            owned = self in self.state.operations
            self.state.operations.discard(self)
        if owned:
            super().end(error)

    def failure(self, label):
        if self.span.is_recording():
            safe(self.span.set_attribute, ai.ERROR_TYPE, label)
            safe(self.span.set_status, Status(StatusCode.ERROR, label))

    def output_text(self, text, *, delta=False):
        if (
            type(text) is not str
            or not self.span.is_recording()
            or not self.state.rt.policy.enabled
        ):
            return
        limit = self.state.rt.policy.max_bytes
        self.text = (
            (self.text + text[: max(0, limit - len(self.text))])
            if delta
            else text[:limit]
        )
        safe(self.output, self.text)


def patch(target, name, wrapper, undo):
    original = getattr(target, name, None)
    if original is None or (
        isinstance(
            original,
            (wrapt.ObjectProxy, wrapt.FunctionWrapper, wrapt.BoundFunctionWrapper),
        )
    ):
        return
    owned = name in vars(target)
    patched = wrapt.FunctionWrapper(original, wrapper)
    setattr(target, name, patched)

    def restore():
        if vars(target).get(name) is patched:
            if owned:
                setattr(target, name, original)
            else:
                delattr(target, name)

    undo.append(restore)


def stream(op, value, consume):
    op.streaming = True
    cls = AsyncStream if hasattr(value, "__anext__") else Stream
    return cls(value, operation=op, consume=consume)


def execution_wrapper(state, describe, request, response, *, finish=None):
    def start(wrapped, instance, args, kwargs):
        params = inspect.signature(wrapped).bind_partial(*args, **kwargs).arguments
        description = describe(instance, params)
        if description is None:
            return None
        name, attributes = description
        op = state.start(name, attributes)
        safe(request, op, instance, params)
        return op

    def complete(op, value, instance):
        if inspect.iscoroutine(value):

            async def awaited():
                try:
                    with op.active():
                        result = await value
                    return complete(op, result, instance)
                except BaseException as error:
                    op.end(error)
                    raise

            return awaited()
        if hasattr(value, "__next__") or hasattr(value, "__anext__"):
            return stream(op, value, response)
        deferred = safe(finish, op, value, instance) if finish else False
        if not deferred:
            safe(response, op, value)
            op.end()
        return value

    def wrapper(wrapped, instance, args, kwargs):
        if not state.enabled():
            return wrapped(*args, **kwargs)
        op = safe(start, wrapped, instance, args, kwargs)
        if op is None:
            return wrapped(*args, **kwargs)
        try:
            with op.active():
                result = wrapped(*args, **kwargs)
            return complete(op, result, instance)
        except BaseException as error:
            op.end(error)
            raise

    return wrapper
