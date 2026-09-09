"""Private request context and independently batched project destinations."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from contextlib import AbstractContextManager

from opentelemetry import context, trace
from opentelemetry.context import _SUPPRESS_INSTRUMENTATION_KEY
from opentelemetry.sdk.environment_variables import OTEL_SDK_DISABLED
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .. import _attributes as confident

log = logging.getLogger(confident.SCOPE_NAME)

_TRACE_CONTEXT = context.create_key(confident.TRACE_CONTEXT_KEY)
_DEFER_TRACE_CONTEXT = context.create_key(confident.DEFER_TRACE_CONTEXT_KEY)

_ROUTE = context.create_key(confident.PROJECT_CONTEXT_KEY)
_SUPPRESS = context.create_key(confident.SUPPRESS_CONTEXT_KEY)


def suppressed(ctx=None):
    return bool(
        context.get_value(_SUPPRESS, ctx)
        or context.get_value(_SUPPRESS_INSTRUMENTATION_KEY, ctx)
    )


def detached_context():
    current = context.get_current()
    result = context.Context()
    for key in (_ROUTE, _SUPPRESS, _SUPPRESS_INSTRUMENTATION_KEY, _TRACE_CONTEXT):
        result = context.set_value(key, current.get(key), result)
    return result


class _Scope(AbstractContextManager):
    def __init__(self, api_key=None, trace_values=None):
        self.trace_values = trace_values
        self.api_key = api_key
        self.token = None
        self.manager = None
        self.route = None

    def __enter__(self):
        if self.token is not None:
            raise RuntimeError("Create a fresh request scope for each entry")
        ctx = context.get_current()
        if self.trace_values is not None:
            from .spans import _prepare_trace_context

            ctx = _prepare_trace_context(ctx, self.trace_values)
        elif self.api_key is None:
            ctx = context.set_value(_SUPPRESS, True, ctx)
            ctx = context.set_value(_SUPPRESS_INSTRUMENTATION_KEY, True, ctx)
        elif os.getenv(OTEL_SDK_DISABLED, "").lower() != "true":
            from .runtime import current

            rt = current()
            if not rt or not rt.active:
                raise RuntimeError(
                    "Initialize Confident Trace before selecting a project"
                )
            self.manager = rt.processor.delegate
            self.route = self.manager.acquire(self.api_key, ctx)
            ctx = context.set_value(_ROUTE, self.route, ctx)
        self.token = context.attach(ctx)
        return self

    def __exit__(self, *exc):
        context.detach(self.token)
        self.token = None
        if self.manager:
            self.manager.release(self.route)

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *exc):
        return self.__exit__(*exc)


def suppress_tracing():
    """Suppress supported instrumentation in this sync/async request scope."""
    return _Scope()


def project(*, api_key):
    """Choose a project before starting traced work; credentials stay private."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a nonempty string")
    return _Scope(api_key)


class _Route:
    def __init__(self, exporter, key=None):
        self.key = key
        self.active = self.leases = self.generation = 0
        self.dirty = False
        self.closed = False
        self.processor = BatchSpanProcessor(exporter)


class RoutingProcessor:
    """Route at span start, export at span end; never buffer a whole trace.

    Only idle routes are bounded. Active requests and queued spans cannot be
    evicted. The standard batch processor retains its normal bounded queue.
    """

    def __init__(self, exporter, factory=None, default_key=None):
        self.span_exporter = exporter
        self.default = _Route(exporter)
        self.factory, self.default_key = factory, default_key
        self.routes = OrderedDict()
        self.spans = {}
        self.retirements = set()
        self.lock = threading.RLock()
        self.closed = False

    def acquire(self, key, ctx):
        with self.lock:
            if self.closed:
                raise RuntimeError("Tracing is shut down")
            previous = context.get_value(_ROUTE, ctx) or self.default
            existing = self.default if key == self.default_key else self.routes.get(key)
            if trace.get_current_span(ctx).is_recording() and existing is not previous:
                raise RuntimeError("Select the project before starting traced work")
            if existing is None:
                if self.factory is None:
                    raise RuntimeError(
                        "Project routing requires project_exporter_factory with a custom exporter"
                    )
                try:
                    existing = _Route(self.factory(key), key)
                except Exception:
                    raise RuntimeError("Project exporter creation failed") from None
                self.routes[key] = existing
            if key in self.routes:
                self.routes.move_to_end(key)
            existing.leases += 1
            self._trim()
            return existing

    def _trim(self, timeout_millis=1000):
        deadline = time.monotonic() + timeout_millis / 1000
        idle = [(k, r) for k, r in self.routes.items() if not (r.active or r.leases)]
        for key, route in idle[:-64]:
            try:
                if route.dirty:
                    remaining = int((deadline - time.monotonic()) * 1000)
                    if remaining <= 0 or not route.processor.force_flush(remaining):
                        continue
                route.dirty = False
                del self.routes[key]
                route.closed = True

                # Custom exporter shutdown must not block or replace a request result.
                def retire(value=route):
                    try:
                        value.processor.shutdown()
                    except Exception:
                        log.debug("Project exporter cleanup failed")
                    finally:
                        with self.lock:
                            self.retirements.discard(threading.current_thread())

                worker = threading.Thread(target=retire, daemon=True)
                self.retirements.add(worker)
                worker.start()
            except Exception:
                log.debug("Project exporter cleanup failed")

    def release(self, route):
        with self.lock:
            route.leases -= 1
            self._trim()

    def on_start(self, span, parent_context=None):
        with self.lock:
            if self.closed:
                return
            route = (
                None
                if suppressed(parent_context)
                else (context.get_value(_ROUTE, parent_context) or self.default)
            )
            if route and route.closed:
                # A generator or detached child task can retain an old scope.
                try:
                    route = self.acquire(route.key, context.Context())
                    route.leases -= 1
                except RuntimeError:
                    route = None  # Never send a failed project route to the default.
            self.spans[span.context.span_id] = route
            if route:
                route.active += 1

    def on_end(self, span):
        with self.lock:
            route = self.spans.pop(span.context.span_id, None)
            if route is None or self.closed:
                return
            route.active -= 1
            route.generation += 1
            route.dirty = True
            route.processor.on_end(span)

    def force_flush(self, timeout_millis=30000):
        deadline = time.monotonic() + timeout_millis / 1000
        with self.lock:
            routes = [self.default, *self.routes.values()]
        result = True
        for route in routes:
            with self.lock:
                generation = route.generation
            flushed = route.processor.force_flush(
                max(0, int((deadline - time.monotonic()) * 1000))
            )
            result = flushed and result
            if flushed:
                with self.lock:
                    if generation == route.generation:
                        route.dirty = False
        with self.lock:
            self._trim(max(0, int((deadline - time.monotonic()) * 1000)))
        return result

    def shutdown(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            routes = [self.default, *self.routes.values()]
            self.routes.clear()
            self.spans.clear()
            retirements = list(self.retirements)
        for route in routes:
            try:
                route.processor.shutdown()
            except Exception:
                log.debug("Project exporter shutdown failed")
        for worker in retirements:
            worker.join()
