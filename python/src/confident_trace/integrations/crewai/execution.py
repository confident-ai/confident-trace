"""Invocation-local scopes; no event-bus callbacks or mutable current-run field."""

import inspect
import os
import threading
import weakref
from contextlib import contextmanager
from contextvars import ContextVar, copy_context

import wrapt

from ..._core import runtime
from ..._core.safety import safe
from ..._core.spans import Operation
from ..._semconv import genai_v1_37_0 as ai
from . import extraction

_CALL = ContextVar("crewai_tool_call", default=None)
_STATES = weakref.WeakSet()


def _after_fork():
    for state in tuple(_STATES):
        state.lock = threading.RLock()
        state.operations = set()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


class State:
    def __init__(self, rt):
        self.rt = rt
        self.lock = threading.RLock()
        self.operations = set()
        self.closed = False
        try:
            from crewai.flow.async_feedback import HumanFeedbackPending

            self.control_flow = (HumanFeedbackPending,)
        except ImportError:
            self.control_flow = ()
        _STATES.add(self)

    def enabled(self):
        return (
            not self.closed
            and self.rt is runtime.current()
            and self.rt.active
            and not runtime.disabled()
        )

    def begin(self, kind, instance, params, args, kwargs):
        name, attributes = extraction.describe(kind, instance, params)
        if kind == "tool":
            call = _CALL.get()
            if call is not None and call[0] == attributes.get(ai.GEN_AI_TOOL_NAME):
                if type(call[1]) is str:
                    attributes[ai.GEN_AI_TOOL_CALL_ID] = call[1]
        op = Operation(name, attributes=attributes)
        with self.lock:
            closed = self.closed
            if not closed:
                self.operations.add(op)
        if closed:
            op.end()
            return None
        safe(extraction.request, op, kind, instance, params, args, kwargs)
        return op

    def finish(self, op, error=None):
        with self.lock:
            owned = op in self.operations
            self.operations.discard(op)
        if owned:
            op.end(None if isinstance(error, self.control_flow) else error)

    def close(self):
        with self.lock:
            self.closed = True
            operations, self.operations = self.operations, set()
        for op in operations:
            op.end()


class _ToolProxy(wrapt.ObjectProxy):
    """One invocation's func binding for CrewStructuredTool's plain executor.

    The upstream lambda reads self.func in its worker, after context was lost.
    Rebinding that one method to a transparent proxy lets the original method
    dispatch unchanged, without changing the shared tool or Python executors.
    """

    def __init__(self, tool):
        super().__init__(tool)
        func = tool.func
        ctx = copy_context()
        self._self_func = (
            func
            if inspect.iscoroutinefunction(func)
            else lambda *a, **kw: ctx.copy().run(func, *a, **kw)
        )

    @property
    def func(self):
        return self._self_func


def parameters(wrapped, args, kwargs):
    return inspect.signature(wrapped).bind(*args, **kwargs).arguments


@contextmanager
def scope(state, op, kind):
    try:
        with op.active():
            token = _CALL.set(None) if kind == "tool" else None
            try:
                yield
            finally:
                if token is not None:
                    _CALL.reset(token)
    except BaseException as error:
        state.finish(op, error)
        raise
    finally:
        state.finish(op)


def execution_wrapper(state, kind, original, *, structured_async=False):
    def begin(wrapped, instance, args, kwargs):
        if not state.enabled():
            return None
        # Streaming kickoff returns a handle; its inner invocation does the work
        # under the consuming execution context and gets the single crew/flow span.
        if kind in ("crew", "flow") and getattr(instance, "stream", False):
            return None
        params = safe(parameters, wrapped, args, kwargs)
        if params is None:
            return None
        return safe(state.begin, kind, instance, params, args, kwargs)

    if inspect.iscoroutinefunction(original):

        async def asynchronous(wrapped, instance, args, kwargs):
            op = safe(begin, wrapped, instance, args, kwargs)
            if op is None:
                return await wrapped(*args, **kwargs)
            with scope(state, op, kind):
                proxy = safe(_ToolProxy, instance) if structured_async else None
                if proxy is not None:
                    result = await original(proxy, *args, **kwargs)
                else:
                    result = await wrapped(*args, **kwargs)
                safe(extraction.response, op, kind, result)
                safe(tool_failure, op, kind, result)
                return result

        return asynchronous

    def synchronous(wrapped, instance, args, kwargs):
        op = safe(begin, wrapped, instance, args, kwargs)
        if op is None:
            return wrapped(*args, **kwargs)
        with scope(state, op, kind):
            result = wrapped(*args, **kwargs)
            safe(extraction.response, op, kind, result)
            safe(tool_failure, op, kind, result)
            return result

    return synchronous


def tool_failure(op, kind, result):
    if kind != "tool" or not op.span.is_recording():
        return
    from crewai.tools.tool_failure import ToolFailure
    from opentelemetry.trace import Status, StatusCode

    if isinstance(result, ToolFailure):
        op.span.set_attribute(ai.ERROR_TYPE, "ToolFailure")
        op.span.set_status(Status(StatusCode.ERROR, "ToolFailure"))


def call_context(wrapped, instance, args, kwargs):
    # Executor handles exceptions and caching. Trace actual tool execution below
    # it; carry the explicit call ID without creating a second execute_tool span.
    params = safe(parameters, wrapped, args, kwargs) or {}
    name, call_id = safe(extraction.tool_identity, params) or (None, None)
    token = _CALL.set((name, call_id))
    try:
        return wrapped(*args, **kwargs)
    finally:
        _CALL.reset(token)
