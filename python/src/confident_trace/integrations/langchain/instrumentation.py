"""Owned callback registration and bounded context hooks for both frameworks."""

import importlib

import wrapt

from ..._core.safety import safe
from .callback import Bridge
from .execution import config_wrapper, frame_wrapper, model_wrapper


def patch(target, name, wrapper, undo):
    original = getattr(target, name, None)
    if original is None or isinstance(original, wrapt.ObjectProxy):
        return
    patched = wrapt.FunctionWrapper(original, wrapper)
    setattr(target, name, patched)

    def restore():
        if vars(target).get(name) is patched:
            setattr(target, name, original)

    undo.append(restore)


def instrument(rt):
    previous = getattr(rt, "_langchain_bridge", None)
    if previous is not None and not previous.closed:
        return []
    bridge = Bridge(rt)
    rt._langchain_bridge = bridge
    undo = []

    def configure(wrapped, instance, args, kwargs):
        manager = wrapped(*args, **kwargs)
        if bridge.enabled() and not any(h is bridge for h in manager.handlers):
            safe(manager.add_handler, bridge, inherit=True)
        return manager

    try:
        module = importlib.import_module("langchain_core.callbacks.manager")
        patch(module, "_configure", configure, undo)
        for path in (
            "langchain_core.runnables.config",
            "langchain_core.runnables.base",
            "langchain_core.tools.base",
        ):
            patch(
                importlib.import_module(path),
                "set_config_context",
                config_wrapper(bridge),
                undo,
            )
        try:
            graph = importlib.import_module("langgraph._internal._runnable")
            from langgraph.errors import GraphBubbleUp

            bridge.control_flow += (GraphBubbleUp,)
            patch(graph, "set_config_context", config_wrapper(bridge), undo)
            # Python 3.10 cannot run a coroutine in an explicit Context. These
            # invocation scopes also cover nodes whose callable disables tracing.
            for cls_name in ("RunnableCallable", "RunnableSeq"):
                cls = getattr(graph, cls_name, None)
                if cls is not None:
                    for method, mode in (("invoke", "sync"), ("ainvoke", "async")):
                        patch(
                            cls,
                            method,
                            frame_wrapper(bridge, ("chain", "invoke_agent"), mode),
                            undo,
                        )
        except ImportError:
            pass
        for path, cls_name, helpers in (
            (
                "langchain_core.language_models.chat_models",
                "BaseChatModel",
                ("_generate_with_cache", "_agenerate_with_cache"),
            ),
            (
                "langchain_core.language_models.llms",
                "BaseLLM",
                ("_generate_helper", "_agenerate_helper"),
            ),
        ):
            cls = getattr(importlib.import_module(path), cls_name)
            for method in helpers:
                patch(cls, method, model_wrapper(bridge), undo)
            for method in ("stream", "astream"):
                patch(
                    cls,
                    method,
                    frame_wrapper(bridge, ("chat", "text_completion"), method),
                    undo,
                )
        cls = importlib.import_module("langchain_core.runnables.base").Runnable
        for method, mode in (
            ("_call_with_config", "sync"),
            ("_acall_with_config", "async"),
            ("_transform_stream_with_config", "stream"),
            ("_atransform_stream_with_config", "astream"),
        ):
            patch(
                cls,
                method,
                frame_wrapper(bridge, ("chain", "invoke_agent"), mode),
                undo,
            )
        cls = importlib.import_module("langchain_core.tools.base").BaseTool
        for method, mode in (("run", "sync"), ("arun", "async")):
            patch(cls, method, frame_wrapper(bridge, ("execute_tool",), mode), undo)
        cls = importlib.import_module("langchain_core.retrievers").BaseRetriever
        for method, mode in (("invoke", "sync"), ("ainvoke", "async")):
            patch(cls, method, frame_wrapper(bridge, ("retriever",), mode), undo)
    except Exception:
        for restore in reversed(undo):
            safe(restore)
        bridge.close()
        raise
    # Close first during reverse-order shutdown, then restore only owned patches.
    return [*undo, bridge.close]
