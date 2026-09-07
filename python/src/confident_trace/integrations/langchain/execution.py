"""Bound OTel activation to execution, never the lifetime between callbacks.

Config helpers yield an isolated context. Model helpers expose a run manager;
public model streams instead bind the start callback to one iterator-local frame.
Private hooks are deliberately confined to this module and tested with real SDKs.
"""

import asyncio
import inspect
import threading
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry import context

from ..._core.safety import safe
from .._shared.lifecycle import _SUPPRESS

_FRAME = ContextVar("langchain_execution_frame", default=None)


def owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return threading.get_ident(), task


def run_context(run):
    ctx = run.operation.ctx
    if (
        run.kind in ("chat", "text_completion")
        and run.operation.span.get_span_context().is_valid
    ):
        ctx = context.set_value(_SUPPRESS, True, ctx)
    return ctx


class Frame:
    def __init__(self, bridge, kinds):
        self.bridge = bridge
        self.kinds = kinds
        self.run_id = None
        self.run = None

    @contextmanager
    def active(self):
        tokens = []
        token = _FRAME.set((self, owner(), tokens))
        if self.run is not None:
            tokens.append(context.attach(run_context(self.run)))
        try:
            yield
        finally:
            for item in reversed(tokens):
                context.detach(item)
            _FRAME.reset(token)

    def finish(self, error=None):
        if self.run_id is not None:
            safe(self.bridge.finish, self.run_id, error=error)


def bind(bridge, run_id, run):
    binding = _FRAME.get()
    if binding is None:
        return
    frame, scope_owner, tokens = binding
    if (
        frame.bridge is not bridge
        or run.kind not in frame.kinds
        or frame.run_id is not None
    ):
        return
    # An inherited frame is not permission to change a different task's context.
    if scope_owner != owner():
        return
    frame.run_id, frame.run = run_id, run
    tokens.append(context.attach(run_context(run)))


class Iterator:
    def __init__(self, wrapped, frame):
        self.wrapped = wrapped
        self.frame = frame

    def __iter__(self):
        return self

    def __next__(self):
        return self.drive("__next__")

    def send(self, value):
        return self.drive("send", value)

    def throw(self, *args):
        return self.drive("throw", *args)

    def drive(self, method, *args):
        with self.frame.active():
            try:
                return getattr(self.wrapped, method)(*args)
            except BaseException as error:
                self.frame.finish(error)
                raise

    def close(self):
        with self.frame.active():
            try:
                close = getattr(self.wrapped, "close", None)
                if close:
                    return close()
            finally:
                self.frame.finish()


class AsyncIterator:
    def __init__(self, wrapped, frame):
        self.wrapped = wrapped
        self.frame = frame

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.drive("__anext__")

    async def asend(self, value):
        return await self.drive("asend", value)

    async def athrow(self, *args):
        return await self.drive("athrow", *args)

    async def drive(self, method, *args):
        with self.frame.active():
            try:
                return await getattr(self.wrapped, method)(*args)
            except BaseException as error:
                self.frame.finish(error)
                raise

    async def aclose(self):
        with self.frame.active():
            try:
                close = getattr(self.wrapped, "aclose", None)
                if close:
                    return await close()
            finally:
                self.frame.finish()


def frame_wrapper(bridge, kinds, mode):
    def wrapper(wrapped, instance, args, kwargs):
        if not bridge.enabled():
            return wrapped(*args, **kwargs)
        frame = Frame(bridge, kinds)
        if mode in ("stream", "astream"):
            value = wrapped(*args, **kwargs)
            cls = Iterator if mode == "stream" else AsyncIterator
            return cls(value, frame)
        if mode == "async":

            async def execute():
                with frame.active():
                    try:
                        return await wrapped(*args, **kwargs)
                    except BaseException as error:
                        frame.finish(error)
                        raise
                    finally:
                        frame.finish()

            return execute()
        with frame.active():
            try:
                return wrapped(*args, **kwargs)
            except BaseException as error:
                frame.finish(error)
                raise
            finally:
                frame.finish()

    return wrapper


def model_wrapper(bridge):
    def wrapper(wrapped, instance, args, kwargs):
        # Bind by signature, including positional run managers. Unknown signatures
        # leave provider tracing enabled rather than suppressing an unowned call.
        params = safe(lambda: inspect.signature(wrapped).bind_partial(*args, **kwargs))
        manager = params.arguments.get("run_manager") if params else None
        managers = params.arguments.get("run_managers") if params else None
        if manager is None and managers:
            manager = managers[0]
        run = (
            bridge.lookup(getattr(manager, "run_id", None))
            if bridge.enabled()
            else None
        )
        if run is None:
            return wrapped(*args, **kwargs)
        if inspect.iscoroutinefunction(wrapped):

            async def execute():
                token = context.attach(run_context(run))
                try:
                    return await wrapped(*args, **kwargs)
                except BaseException as error:
                    safe(bridge.finish, manager.run_id, error=error)
                    raise
                finally:
                    context.detach(token)

            return execute()
        token = context.attach(run_context(run))
        try:
            return wrapped(*args, **kwargs)
        except BaseException as error:
            safe(bridge.finish, manager.run_id, error=error)
            raise
        finally:
            context.detach(token)

    return wrapper


def config_wrapper(bridge):
    def wrapper(wrapped, instance, args, kwargs):
        @contextmanager
        def configured():
            with wrapped(*args, **kwargs) as ctx:
                config = args[0] if args else kwargs.get("config", {})
                manager = config.get("callbacks") if type(config) is dict else None
                run = (
                    bridge.lookup(getattr(manager, "parent_run_id", None))
                    if bridge.enabled()
                    else None
                )
                token = (
                    safe(lambda: ctx.run(context.attach, run.operation.ctx))
                    if run
                    else None
                )
                try:
                    yield ctx
                finally:
                    if token is not None:
                        safe(ctx.run, context.detach, token)

        return configured()

    return wrapper
