"""Convenience APIs over OTel spans and context."""

from __future__ import annotations

import functools
import inspect
from contextlib import contextmanager

from opentelemetry import context
from opentelemetry import trace as otel
from opentelemetry.trace import Status, StatusCode

from .._semconv import genai_v1_37_0 as ai
from . import runtime as _runtime
from .safety import safe

_ENTRY = context.create_key("confident_trace.entry")
_FIELDS = {
    "name",
    "input",
    "output",
    "tags",
    "metadata",
    "environment",
    "user_id",
    "thread_id",
    "turn_id",
}


def content(span, key, value):
    rt = _runtime.current()
    if rt and span.is_recording():
        shape = {
            ai.GEN_AI_INPUT_MESSAGES: "input-messages",
            ai.GEN_AI_OUTPUT_MESSAGES: "output-messages",
            ai.GEN_AI_SYSTEM_INSTRUCTIONS: "system-instructions",
        }.get(key)
        encoded = rt.policy.encode(value, shape=shape)
        if encoded is not None:
            safe(span.set_attribute, key, encoded)


def fields(span, values):
    if not span.is_recording():
        return
    for key, value in values.items():
        if key not in _FIELDS or value is None:
            continue
        attr = "confident.trace." + key
        if key in ("input", "output", "metadata"):
            content(span, attr, value)
        elif key == "tags":
            if type(value) in (list, tuple) and all(type(v) is str for v in value):
                safe(span.set_attribute, attr, value[:128])
        elif type(value) is str:
            safe(span.set_attribute, attr, value[:4096])
            if key == "thread_id":
                safe(span.set_attribute, ai.GEN_AI_CONVERSATION_ID, value[:4096])


def update_trace(**values):
    """Update the active entry span, or the current ordinary OTel span."""
    rt = _runtime.current()
    if not rt or not rt.active or _runtime.disabled():
        return
    span = context.get_value(_ENTRY) or otel.get_current_span()
    safe(fields, span, values)


class Operation:
    def __init__(
        self,
        name,
        *,
        attributes=None,
        kind=otel.SpanKind.INTERNAL,
        trace_fields=None,
    ):
        rt = _runtime.current()
        if not rt or not rt.active or _runtime.disabled():
            self.parent = context.get_current()
            self.ctx = self.parent
            self.span = otel.INVALID_SPAN
            self.is_entry = False
            self.ended = False
            return
        self.parent = context.get_current()
        self.entry = context.get_value(_ENTRY, self.parent)
        self.span = (
            safe(
                _runtime.tracer().start_span,
                name,
                context=self.parent,
                kind=kind,
                attributes=attributes,
            )
            or otel.INVALID_SPAN
        )
        self.is_entry = self.entry is None
        self.ctx = otel.set_span_in_context(self.span, self.parent)
        if self.is_entry:
            self.ctx = context.set_value(_ENTRY, self.span, self.ctx)
            safe(fields, self.span, {"name": name, **(trace_fields or {})})
        # Inherit explicit conversation metadata from the entry without reparenting.
        if self.entry is not None:
            entry_attributes = getattr(self.entry, "attributes", None) or {}
            conversation = entry_attributes.get(ai.GEN_AI_CONVERSATION_ID)
            if conversation:
                safe(self.span.set_attribute, ai.GEN_AI_CONVERSATION_ID, conversation)
        self.ended = False

    @contextmanager
    def active(self):
        token = context.attach(self.ctx)
        try:
            yield self.span
        finally:
            context.detach(token)

    def input(self, value):
        content(self.span, "confident.span.input", value)
        if self.is_entry:
            content(self.span, "confident.trace.input", value)

    def output(self, value):
        content(self.span, "confident.span.output", value)
        if self.is_entry:
            content(self.span, "confident.trace.output", value)

    def end(self, error=None):
        if self.ended:
            return
        self.ended = True
        if error is not None and not isinstance(
            error, (GeneratorExit, StopIteration, StopAsyncIteration)
        ):
            # Exception messages/tracebacks can contain credentials and content.
            safe(self.span.set_attribute, ai.ERROR_TYPE, type(error).__name__)
            safe(self.span.set_status, Status(StatusCode.ERROR, type(error).__name__))
        safe(self.span.end)


@contextmanager
def _span_context(name, *, attributes=None, **trace_fields):
    """Create an ordinary child span and expose the real OTel span."""
    operation = Operation(name, attributes=attributes, trace_fields=trace_fields)
    try:
        with operation.active():
            yield operation.span
    except BaseException as error:
        operation.end(error)
        raise
    finally:
        operation.end()


def _decorate_span(
    func,
    *,
    name=None,
    kind="step",
    capture_content=True,
    attributes=None,
    trace_fields=None,
):
    """Trace a function without replacing its result or changing exceptions."""

    def decorate(fn):
        def start(args, kwargs):
            op = Operation(
                ("execute_tool " if kind == "tool" else "") + (name or fn.__qualname__),
                attributes=_span_attributes(name or fn.__qualname__, kind, attributes),
                trace_fields=trace_fields,
            )
            op.capture_result = capture_content
            if capture_content:
                # Never inspect self, properties, iterators or arbitrary objects.
                safe(op.input, {"args": args, "kwargs": kwargs})
            return op

        if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):

            @functools.wraps(fn)
            def generator(*args, **kwargs):
                rt = _runtime.current()
                if not rt or not rt.active or _runtime.disabled():
                    return fn(*args, **kwargs)
                from ..integrations._shared.streams import AsyncStream, Stream

                value = fn(*args, **kwargs)

                def factory():
                    return start(args, kwargs)

                cls = AsyncStream if inspect.isasyncgenfunction(fn) else Stream
                return cls(value, factory=factory)

            return generator
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def asynchronous(*args, **kwargs):
                rt = _runtime.current()
                if not rt or not rt.active or _runtime.disabled():
                    return await fn(*args, **kwargs)
                op = start(args, kwargs)
                try:
                    with op.active():
                        result = await fn(*args, **kwargs)
                    if capture_content:
                        safe(op.output, result)
                    return result
                except BaseException as error:
                    op.end(error)
                    raise
                finally:
                    op.end()

            return asynchronous

        @functools.wraps(fn)
        def synchronous(*args, **kwargs):
            rt = _runtime.current()
            if not rt or not rt.active or _runtime.disabled():
                return fn(*args, **kwargs)
            op = start(args, kwargs)
            try:
                with op.active():
                    result = fn(*args, **kwargs)
                if capture_content:
                    safe(op.output, result)
                return result
            except BaseException as error:
                op.end(error)
                raise
            finally:
                op.end()

        return synchronous

    return decorate(func) if func is not None else decorate


def _span_attributes(name, kind, attributes):
    result = dict(attributes or {})
    if kind == "tool":
        result.update(
            {ai.GEN_AI_OPERATION_NAME: "execute_tool", ai.GEN_AI_TOOL_NAME: name}
        )
    return result


class _SpanScope:
    """A configured decorator or a single-use context manager."""

    def __init__(self, name, kind, capture_content, attributes, trace_fields):
        self.name = name
        self.kind = kind
        self.capture_content = capture_content
        self.attributes = attributes
        self.trace_fields = trace_fields
        self._context = None

    def __call__(self, fn):
        return _decorate_span(
            fn,
            name=self.name,
            kind=self.kind,
            capture_content=self.capture_content,
            attributes=self.attributes,
            trace_fields=self.trace_fields,
        )

    def __enter__(self):
        if self._context is not None:
            raise RuntimeError("Create a fresh span() context manager for each use")
        name = self.name or "span"
        self._context = _span_context(
            ("execute_tool " if self.kind == "tool" else "") + name,
            attributes=_span_attributes(name, self.kind, self.attributes),
            **self.trace_fields,
        )
        return self._context.__enter__()

    def __exit__(self, *exc):
        return self._context.__exit__(*exc)


def span(
    func=None,
    *,
    name=None,
    kind="step",
    capture_content=True,
    attributes=None,
    **trace_fields,
):
    """Create a span using @span, @span(name=...), or with span("name").

    Decorators preserve synchronous, asynchronous and generator behavior.
    Context managers yield the underlying OTel span. Both inherit OTel parentage.
    """
    if callable(func):
        return _decorate_span(
            func,
            name=name,
            kind=kind,
            capture_content=capture_content,
            attributes=attributes,
            trace_fields=trace_fields,
        )
    if func is not None:
        if not isinstance(func, str) or name is not None:
            raise TypeError("Pass a function or one span name")
        name = func
    return _SpanScope(name, kind, capture_content, attributes, trace_fields)
