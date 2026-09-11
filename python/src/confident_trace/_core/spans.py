"""Convenience APIs over OTel spans and context."""

from __future__ import annotations

import functools
import inspect
import logging
import math
import warnings
from contextlib import contextmanager
from typing import Literal
from weakref import WeakKeyDictionary

from opentelemetry import context
from opentelemetry import trace as otel
from opentelemetry.trace import Link, Status, StatusCode

from .. import _attributes as confident
from .._semconv import genai_v1_37_0 as ai
from . import runtime as _runtime
from .safety import safe
from .scopes import _Scope, _TRACE_CONTEXT, _DEFER_TRACE_CONTEXT, suppressed

_ENTRY = context.create_key(confident.ENTRY_CONTEXT_KEY)


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


_CONTENT = {
    "input",
    "output",
    "metadata",
    "retrieval_context",
    "context",
    "expected_output",
    "tools_called",
    "expected_tools",
}
_TRACE = set(confident.TRACE_FIELDS) | _CONTENT | {"test_case_id", "thread"}
_SPAN = _CONTENT | {"name", "metric_collection"}
_WRITES = WeakKeyDictionary()
_TYPES = ("agent", "llm", "retriever", "tool", "custom")
SpanType = Literal["agent", "llm", "retriever", "tool", "custom"]
_LLM = {
    "model": ai.GEN_AI_REQUEST_MODEL,
    "provider": ai.GEN_AI_PROVIDER_NAME,
    "input_token_count": ai.GEN_AI_USAGE_INPUT_TOKENS,
    "output_token_count": ai.GEN_AI_USAGE_OUTPUT_TOKENS,
    "cost_per_input_token": confident.LLM_COST_PER_INPUT_TOKEN,
    "cost_per_output_token": confident.LLM_COST_PER_OUTPUT_TOKEN,
}


def validate(values, allowed):
    unknown = set(values) - allowed
    if unknown:
        raise TypeError("Unknown tracing fields: " + ", ".join(sorted(unknown)))
    thread = values.get("thread")
    if thread is not None:
        if not isinstance(thread, dict) or set(thread) - {"id", "tags", "metadata"}:
            raise TypeError("thread accepts id, tags, metadata")
        if "id" in thread and not isinstance(thread["id"], str):
            raise TypeError("thread.id must be a string")
        if (
            "thread_id" in values
            and "id" in thread
            and values["thread_id"] != thread["id"]
        ):
            raise ValueError("Conflicting thread IDs")
    for key, value in values.items():
        if key not in _LLM:
            continue
        if key in ("model", "provider"):
            if not isinstance(value, str):
                raise TypeError(key + " must be a string")
        elif key.endswith("token_count"):
            if type(value) is not int or value < 0:
                raise ValueError(key + " must be a nonnegative integer")
        elif type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(key + " must be finite and nonnegative")


def fields(span, values, *, scope="trace", only_unset=False):
    if not span.is_recording():
        return
    for key, value in values.items():
        if key == "thread" and scope == "trace":
            if only_unset and any(attr in (getattr(span, "attributes", None) or {}) for attr in (confident.THREAD_ID, confident.THREAD_TAGS, confident.THREAD_METADATA, confident.TRACE_THREAD_ID)):
                continue
            for field, item in (value or {}).items():
                if field == "id":
                    fields(span, {"thread_id": item}, only_unset=only_unset)
                elif field == "metadata":
                    content(span, confident.THREAD_METADATA, item)
                elif (
                    field == "tags"
                    and isinstance(item, (list, tuple))
                    and all(isinstance(v, str) for v in item)
                ):
                    safe(span.set_attribute, confident.THREAD_TAGS, item[:128])
            continue
        if key not in (_TRACE if scope == "trace" else _SPAN):
            continue
        if key == "name" and scope == "span":
            if isinstance(value, str):
                safe(span.update_name, value[:4096])
            continue
        attr = (confident.TRACE_FIELDS if scope == "trace" else confident.SPAN_FIELDS)[
            key
        ]
        if only_unset and (attr in (getattr(span, "attributes", None) or {}) or attr in _WRITES.get(span, ())):
            continue
        if key in _CONTENT:
            _WRITES.setdefault(span, set()).add(attr)
            content(span, attr, value)
        elif key == "tags":
            if type(value) in (list, tuple) and all(type(v) is str for v in value):
                safe(span.set_attribute, attr, value[:128])
        elif type(value) is str:
            safe(span.set_attribute, attr, value[:4096])
            if key == "thread_id":
                safe(span.set_attribute, confident.THREAD_ID, value[:4096])
                if (
                    getattr(getattr(span, "instrumentation_scope", None), "name", None)
                    == confident.SCOPE_NAME
                ):
                    safe(span.set_attribute, ai.GEN_AI_CONVERSATION_ID, value[:4096])


def _update(values, scope):
    rt = _runtime.current()
    if not rt or not rt.active or _runtime.disabled():
        return
    current = otel.get_current_span()
    target = (context.get_value(_ENTRY) or current) if scope == "trace" else current
    safe(fields, target, values, scope=scope)
    if scope == "span":
        safe(_update_llm_fields, target, values)


def update_trace(**values):
    """Update entry-span fields; thread fields remain separate from trace metadata."""
    validate(values, _TRACE)
    _update(values, "trace")


def trace_context(**values):
    """Fill unset trace properties in a sync/async scope without creating a span.

    Existing fields and outer defaults win; compound values are never merged.
    update_trace remains the explicit replacement API.
    """
    validate(values, _TRACE)
    return _Scope(trace_values=values)


def _prepare_trace_context(ctx, values):
    defaults = {**values, **(context.get_value(_TRACE_CONTEXT, ctx) or {})}
    rt = _runtime.current()
    if rt and rt.active and not _runtime.disabled() and not suppressed(ctx):
        target = context.get_value(_ENTRY, ctx) or otel.get_current_span(ctx)
        safe(fields, target, defaults, only_unset=True)
    return context.set_value(_TRACE_CONTEXT, defaults, ctx)


def ambient_on_start(span, parent_context=None):
    if _runtime.disabled() or suppressed(parent_context) or context.get_value(_DEFER_TRACE_CONTEXT, parent_context):
        return
    if not otel.get_current_span(parent_context).is_recording():
        safe(fields, span, context.get_value(_TRACE_CONTEXT, parent_context) or {}, only_unset=True)


def update_span(**values):
    """Update the active span. Explicit content wins over automatic capture."""
    validate(values, _SPAN | set(_LLM))
    _update(values, "span")


def _llm_fields(span, values):
    if span.is_recording():
        for key, value in values.items():
            if key in _LLM:
                safe(span.set_attribute, _LLM[key], value)


_warned_llm_targets: set[str] = set()


def _update_llm_fields(span, values):
    if not span.is_recording() or not (set(values) & set(_LLM)):
        return
    attrs = getattr(span, "attributes", None) or {}
    category = attrs.get(confident.SPAN_TYPE)
    model_span = category == "llm" or (
        category is None
        and attrs.get(ai.GEN_AI_OPERATION_NAME)
        in (
            ai.GEN_AI_OPERATION_NAME__CHAT,
            ai.GEN_AI_OPERATION_NAME__GENERATE_CONTENT,
            ai.GEN_AI_OPERATION_NAME__TEXT_COMPLETION,
            ai.GEN_AI_OPERATION_NAME__EMBEDDINGS,
        )
    )
    if model_span:
        _llm_fields(span, values)
    elif (
        category if category in _TYPES else "unclassified"
    ) not in _warned_llm_targets:
        _warned_llm_targets.add(category if category in _TYPES else "unclassified")
        logging.getLogger(confident.SCOPE_NAME).warning(
            "update_span skipped LLM fields: the active span is not an LLM span. "
            "Use span(type='llm') or update inside an instrumented model span. "
            "General span fields were still applied."
        )


def update_llm_span(**values):
    """Compatibility alias for LLM fields; prefer update_span."""
    validate(values, set(_LLM))
    update_span(**values)


class Operation:
    def __init__(
        self,
        name,
        *,
        attributes=None,
        integration: confident.Integration | None = None,
        kind=otel.SpanKind.INTERNAL,
        trace_fields=None,
        span_fields=None,
        parent=None,
        links=(),
    ):
        rt = _runtime.current()
        if not rt or not rt.active or _runtime.disabled():
            self.parent = context.get_current()
            self.ctx = self.parent
            self.span = otel.INVALID_SPAN
            self.is_entry = False
            self.ended = False
            return
        if integration is not None:
            attributes = {
                **(attributes or {}),
                confident.SPAN_INTEGRATION: integration.value,
            }
        self.parent = context.get_current() if parent is None else parent
        self.entry = context.get_value(_ENTRY, self.parent)
        self.span = (
            safe(
                _runtime.tracer().start_span,
                name,
                context=context.set_value(_DEFER_TRACE_CONTEXT, True, self.parent),
                kind=kind,
                attributes=attributes,
                links=links,
            )
            or otel.INVALID_SPAN
        )
        self.is_entry = self.entry is None
        self.ctx = otel.set_span_in_context(self.span, self.parent)
        if self.is_entry:
            self.ctx = context.set_value(_ENTRY, self.span, self.ctx)
            # A local entry can be a child of a remote/application span. Only
            # a true trace root supplies an automatic trace name; explicit
            # update_trace/name fields remain deliberate trace-wide updates.
            parent_span = otel.get_current_span(self.parent).get_span_context()
            defaults = {} if parent_span.is_valid else {"name": name}
            safe(fields, self.span, trace_fields or {})
            safe(fields, self.span, context.get_value(_TRACE_CONTEXT, self.parent) or {}, only_unset=True)
            safe(fields, self.span, defaults, only_unset=True)
        # Inherit explicit conversation metadata from the entry without reparenting.
        if self.entry is not None:
            entry_attributes = getattr(self.entry, "attributes", None) or {}
            conversation = entry_attributes.get(ai.GEN_AI_CONVERSATION_ID)
            if conversation:
                safe(self.span.set_attribute, ai.GEN_AI_CONVERSATION_ID, conversation)
        safe(fields, self.span, span_fields or {}, scope="span")
        _llm_fields(self.span, span_fields or {})
        self.ended = False

    @contextmanager
    def active(self):
        token = context.attach(self.ctx)
        try:
            yield self.span
        finally:
            context.detach(token)

    def input(self, value, *, replace_automatic=False):
        # Streaming callbacks may correct their initial placeholder at end.
        # Only replace fields this operation captured, never explicit user fields.
        captured = getattr(self, "_captured_inputs", set())
        keys = [confident.SPAN_INPUT]
        if self.is_entry:
            keys.append(confident.TRACE_INPUT)
        for key in keys:
            if key in _WRITES.get(self.span, ()):
                continue
            attrs = getattr(self.span, "attributes", None) or {}
            if key not in attrs or (replace_automatic and key in captured):
                content(self.span, key, value)
                captured.add(key)
        self._captured_inputs = captured

    def output(self, value):
        if confident.SPAN_OUTPUT not in _WRITES.get(
            self.span, ()
        ) and confident.SPAN_OUTPUT not in (
            getattr(self.span, "attributes", None) or {}
        ):
            content(self.span, confident.SPAN_OUTPUT, value)
        if (
            self.is_entry
            and confident.TRACE_OUTPUT not in _WRITES.get(self.span, ())
            and confident.TRACE_OUTPUT
            not in (getattr(self.span, "attributes", None) or {})
        ):
            content(self.span, confident.TRACE_OUTPUT, value)

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
def _span_context(
    name, *, attributes=None, span_fields=None, parent=None, links=(), **trace_fields
):
    """Create an ordinary child span and expose the real OTel span."""
    operation = Operation(
        name,
        attributes=attributes,
        trace_fields=trace_fields,
        span_fields=span_fields,
        parent=parent,
        links=links,
    )
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
    span_fields=None,
):
    """Trace a function without replacing its result or changing exceptions."""

    def decorate(fn):
        def start(args, kwargs, parent=None):
            op = Operation(
                (
                    (ai.GEN_AI_OPERATION_NAME__EXECUTE_TOOL + " ")
                    if kind == "tool"
                    else ""
                )
                + (name or fn.__qualname__),
                attributes=_span_attributes(name or fn.__qualname__, kind, attributes),
                trace_fields=trace_fields,
                span_fields=span_fields,
                parent=parent,
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

                parent = context.get_current()

                def factory():
                    return start(args, kwargs, parent)

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
    result = {
        **(attributes or {}),
        confident.SPAN_TYPE: "custom" if kind == "step" else kind,
    }
    if kind == "tool":
        result.update(
            {
                ai.GEN_AI_OPERATION_NAME: ai.GEN_AI_OPERATION_NAME__EXECUTE_TOOL,
                ai.GEN_AI_TOOL_NAME: name,
            }
        )
    return result


class _SpanScope:
    """A configured decorator or a single-use context manager."""

    def __init__(
        self,
        name,
        kind,
        capture_content,
        attributes,
        trace_fields,
        span_fields=None,
        parent=None,
        links=(),
    ):
        self.name = name
        self.kind = kind
        self.capture_content = capture_content
        self.attributes = attributes
        self.trace_fields = trace_fields
        self.span_fields = span_fields
        self.parent = parent
        self.links = links
        self._context = None

    def __call__(self, fn):
        return _decorate_span(
            fn,
            name=self.name,
            kind=self.kind,
            capture_content=self.capture_content,
            attributes=self.attributes,
            trace_fields=self.trace_fields,
            span_fields=self.span_fields,
        )

    def __enter__(self):
        if self._context is not None:
            raise RuntimeError("Create a fresh span() context manager for each use")
        name = self.name or "span"
        self._context = _span_context(
            (
                (ai.GEN_AI_OPERATION_NAME__EXECUTE_TOOL + " ")
                if self.kind == "tool"
                else ""
            )
            + name,
            attributes=_span_attributes(name, self.kind, self.attributes),
            span_fields=self.span_fields,
            parent=self.parent,
            links=self.links,
            **self.trace_fields,
        )
        return self._context.__enter__()

    def __exit__(self, *exc):
        return self._context.__exit__(*exc)

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *exc):
        return self.__exit__(*exc)


def span(
    func=None,
    *,
    name=None,
    type: SpanType | None = None,
    kind=None,
    capture_content=True,
    attributes=None,
    **values,
):
    """Decorate a function or enter a sync/async application span scope."""
    validate(values, _SPAN | _TRACE | set(_LLM))
    if kind is not None:
        if kind not in ("step", "tool"):
            raise ValueError("kind accepts step or tool; use type for categories")
        warnings.warn("kind is deprecated; use type", DeprecationWarning, stacklevel=2)
        alias = "custom" if kind == "step" else "tool"
        if type is not None and type != alias:
            raise ValueError("Conflicting type and kind")
        type = alias
    category = (
        type
        if type is not None
        else (attributes or {}).get(confident.SPAN_TYPE, "custom")
    )
    if category not in _TYPES:
        raise ValueError("Invalid span type")
    if set(values) & set(_LLM) and category != "llm":
        raise ValueError("Model and usage options require type='llm'")
    span_values = {k: v for k, v in values.items() if k in _SPAN or k in _LLM}
    trace_values = {k: v for k, v in values.items() if k in _TRACE and k not in _SPAN}
    if callable(func):
        return _decorate_span(
            func,
            name=name,
            kind=category,
            capture_content=capture_content,
            attributes=attributes,
            trace_fields=trace_values,
            span_fields=span_values,
        )
    if func is not None:
        if not isinstance(func, str) or name is not None:
            raise TypeError("Pass a function or one span name")
        name = func
    return _SpanScope(
        name, category, capture_content, attributes, trace_values, span_values
    )


def turn(name="agent turn", *, thread_id=None, previous=None, **values):
    """Start a new trace for a conversation turn, preserving request scopes."""
    if thread_id is not None:
        values["thread_id"] = thread_id
    validate(values, _TRACE)
    if not isinstance(
        thread_id if thread_id is not None else (values.get("thread") or {}).get("id"),
        str,
    ):
        raise TypeError("turn requires thread_id or thread.id")
    from .scopes import detached_context

    links = [Link(previous)] if previous is not None and previous.is_valid else []
    return _SpanScope(
        name, "custom", False, None, values, parent=detached_context(), links=links
    )
