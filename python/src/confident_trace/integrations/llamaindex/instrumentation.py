"""Owned dispatcher registration and existing/future decorated execution scopes."""

import sys

from llama_index_instrumentation import DispatcherSpanMixin, get_dispatcher
from llama_index_instrumentation.dispatcher import Dispatcher

from ..._core.safety import safe
from .._shared.execution import patch
from .bridge import Bridge


def instrument(rt, *, dispatcher=None, include_llm=False):
    previous = getattr(rt, "_llamaindex_bridge", None)
    if previous is not None and not previous.closed:
        undo = []
        old_include_llm = getattr(previous, "include_llm", False)
        if include_llm and not old_include_llm:
            previous.include_llm = True
            undo.append(lambda: setattr(previous, "include_llm", old_include_llm))
        if dispatcher is not None and previous.handler not in dispatcher.span_handlers:
            dispatcher.add_span_handler(previous.handler)

            def remove_dispatcher():
                dispatcher.span_handlers[:] = [
                    handler
                    for handler in dispatcher.span_handlers
                    if handler is not previous.handler
                ]

            undo.append(remove_dispatcher)
        return undo
    bridge = Bridge(rt)
    bridge.include_llm = include_llm
    rt._llamaindex_bridge = bridge
    root = dispatcher if dispatcher is not None else get_dispatcher()
    root.add_span_handler(bridge.handler)
    undo = []

    def decorate(wrapped, instance, args, kwargs):
        result = wrapped(*args, **kwargs)
        if bridge.enabled():
            safe(bridge.bind, result)
        return result

    def remove():
        root.span_handlers[:] = [
            h for h in root.span_handlers if h is not bridge.handler
        ]

    try:
        patch(Dispatcher, "span", decorate, undo)
        # Native decorators may predate init. Scan loaded framework definitions
        # and mixin subclasses without reading properties or importing providers.
        classes = list(DispatcherSpanMixin.__subclasses__())
        seen = set()
        for name, module in tuple(sys.modules.items()):
            if name.startswith(("llama_index.", "workflows.")) or name == "__main__":
                for value in tuple(vars(module).values()) if module else ():
                    safe(bridge.bind, value)
                    if isinstance(value, type) and value.__module__.startswith(
                        ("llama_index.", "workflows.")
                    ):
                        classes.append(value)
        while classes:
            cls = classes.pop()
            if cls in seen:
                continue
            seen.add(cls)
            for value in tuple(vars(cls).values()):
                safe(bridge.bind, value)
            classes.extend(type.__subclasses__(cls))
    except Exception:
        remove()
        bridge.close()
        for restore in reversed(undo):
            safe(restore)
        raise
    return [*undo, remove, bridge.close]
