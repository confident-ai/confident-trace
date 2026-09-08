"""Native span IDs determine hierarchy; activation happens inside the callable."""

import inspect
import weakref
from contextlib import contextmanager, nullcontext

import wrapt
from llama_index_instrumentation.span import active_span_id
from llama_index_instrumentation.span_handlers import BaseSpanHandler
from opentelemetry import context
from pydantic import PrivateAttr

from ..._attributes import Integration
from ..._core.safety import safe
from ..._semconv import genai_v1_37_0 as ai
from .._shared.execution import State, stream
from . import extraction


class Handler(BaseSpanHandler):
    _bridge = PrivateAttr()

    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge

    def span_enter(self, **kwargs):
        safe(self._bridge.enter, **kwargs)

    def span_exit(self, **kwargs):
        safe(self._bridge.exit, **kwargs)

    def span_drop(self, **kwargs):
        safe(self._bridge.exit, **kwargs)

    def new_span(self, **kwargs):
        return None

    def prepare_to_exit_span(self, **kwargs):
        return None

    def prepare_to_drop_span(self, **kwargs):
        return None

    def close(self):
        self._bridge.close()


class Bridge(State):
    def __init__(self, rt):
        super().__init__(rt, integration=Integration.LLAMAINDEX)
        self.runs = {}
        self.claimed = set()
        self.natives = weakref.WeakSet()
        self.handler = Handler(self)

    def after_fork(self):
        super().after_fork()
        self.runs = {}
        self.claimed = set()

    def enter(self, id_, bound_args, instance=None, parent_id=None, **kwargs):
        if not self.enabled():
            return
        description = extraction.describe(
            id_, instance, include_llm=getattr(self, "include_llm", False)
        )
        if description is None:
            return
        # Native chat methods may delegate to complete/stream_complete. Keep
        # one inference operation, while preserving ordinary nested tool spans.
        if description[1].get(ai.GEN_AI_OPERATION_NAME) == "chat":
            from opentelemetry import trace

            current = trace.get_current_span()
            if (getattr(current, "attributes", None) or {}).get(
                ai.GEN_AI_OPERATION_NAME
            ) == "chat":
                return
        with self.lock:
            if id_ in self.claimed:
                return
            self.claimed.add(id_)
            parent = self.runs.get(parent_id)
        token = context.attach(parent.ctx) if parent else None
        try:
            op = self.start(*description)
            if description[1].get(ai.GEN_AI_OPERATION_NAME) == "chat":
                from .._shared.lifecycle import _SUPPRESS

                op.ctx = context.set_value(_SUPPRESS, True, op.ctx)
            with self.lock:
                if not self.closed:
                    self.runs[id_] = op
            safe(extraction.request, op, bound_args)
        except BaseException:
            with self.lock:
                self.claimed.discard(id_)
            raise
        finally:
            if token is not None:
                context.detach(token)

    def exit(self, id_, result=None, err=None, **kwargs):
        with self.lock:
            op = self.runs.get(id_)
            if op is None:
                return
            if err is None and (
                hasattr(result, "__next__") or hasattr(result, "__anext__")
            ):
                return  # The owned iterator closes the operation while being driven.
            self.runs.pop(id_, None)
            self.claimed.discard(id_)
        if err is None:
            safe(extraction.response, op, result)
        op.end(err)

    def execute(self, wrapped, instance, args, kwargs):
        with self.lock:
            op = self.runs.get(active_span_id.get()) if self.enabled() else None
        if inspect.iscoroutinefunction(wrapped):

            async def awaited():
                with op.active() if op else nullcontext():
                    result = await wrapped(*args, **kwargs)
                if op and (hasattr(result, "__next__") or hasattr(result, "__anext__")):
                    return _iterator(self, op, result)
                return result

            return awaited()
        with op.active() if op else nullcontext():
            result = wrapped(*args, **kwargs)
        if op and (hasattr(result, "__next__") or hasattr(result, "__anext__")):
            # Remove native bookkeeping at iterator termination as well as shutdown.
            return _iterator(self, op, result)
        return result

    def bind(self, value):
        # Only enter underneath the framework's own dispatcher decorator. Leave
        # other decorators in place, including existing application callbacks.
        for _ in range(12):
            if not isinstance(
                value,
                (wrapt.ObjectProxy, wrapt.FunctionWrapper, wrapt.BoundFunctionWrapper),
            ):
                return
            wrapper = getattr(value, "_self_wrapper", None)
            if (
                getattr(wrapper, "__module__", "")
                == "llama_index_instrumentation.dispatcher"
            ):
                inner = value.__wrapped__
                if getattr(inner, "_self_wrapper", None) == self.execute:
                    return
                value.__wrapped__ = wrapt.FunctionWrapper(inner, self.execute)
                self.natives.add(value)
                return
            value = value.__wrapped__

    def close(self):
        super().close()
        with self.lock:
            self.runs.clear()
            self.claimed.clear()
        for native in tuple(self.natives):
            inner = native.__wrapped__
            if getattr(inner, "_self_wrapper", None) == self.execute:
                native.__wrapped__ = inner.__wrapped__
        self.natives.clear()


class _IteratorOperation:
    """Adapter for shared streams; release dispatcher IDs on every terminal path."""

    def __init__(self, bridge, op):
        self.bridge, self.op = bridge, op
        self.id = active_span_id.get()

    def __getattr__(self, name):
        return getattr(self.op, name)

    @contextmanager
    def active(self):
        token = active_span_id.set(self.id)
        try:
            with self.op.active() as span:
                yield span
        finally:
            active_span_id.reset(token)

    def end(self, error=None):
        with self.bridge.lock:
            if self.bridge.runs.get(self.id) is self.op:
                self.bridge.runs.pop(self.id, None)
                self.bridge.claimed.discard(self.id)
        self.op.end(error)


def _iterator(bridge, op, value):
    return stream(_IteratorOperation(bridge, op), value, extraction.response)
