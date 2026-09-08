"""Callback IDs own hierarchy; callbacks never own cross-context OTel tokens."""

import os
import threading
import weakref
from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry import context, trace

from ..._attributes import Integration
from ..._core import runtime
from ..._core.safety import safe
from ..._core.spans import Operation
from ..._semconv import genai_v1_37_0 as ai
from . import extraction

_BRIDGES = weakref.WeakSet()


def _after_fork():
    for bridge in tuple(_BRIDGES):
        bridge.lock = threading.RLock()
        # Parent-process operations must not be exported again by the child.
        bridge.runs.clear()
        bridge.claimed.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


@dataclass
class Run:
    operation: Operation
    kind: str


class Bridge(BaseCallbackHandler):
    run_inline = True
    raise_error = False

    def __init__(self, rt):
        self.runtime = rt
        self.lock = threading.RLock()
        self.runs = {}
        self.claimed = set()
        self.closed = False
        self.control_flow = (GeneratorExit,)
        _BRIDGES.add(self)

    def enabled(self):
        return not self.closed and self.runtime.active and not runtime.disabled()

    def lookup(self, run_id):
        with self.lock:
            return self.runs.get(run_id)

    def start(self, kind, serialized, value, run_id, parent_run_id=None, **options):
        if not self.enabled():
            return
        with self.lock:
            if run_id in self.claimed:
                return
            self.claimed.add(run_id)
            parent = self.runs.get(parent_run_id)
        try:
            title = extraction.name(serialized, options)
            attrs = {}
            if kind in ("chat", "text_completion", "execute_tool", "invoke_agent"):
                attrs[ai.GEN_AI_OPERATION_NAME] = kind
            if kind == "retriever":
                attrs[ai.GEN_AI_OPERATION_NAME] = "retrieval"
            if kind == "execute_tool":
                attrs[ai.GEN_AI_TOOL_NAME] = title
            # The token is local to this synchronous callback, never saved on a run.
            token = context.attach(
                parent.operation.ctx if parent else context.get_current()
            )
            try:
                op = Operation(
                    title,
                    attributes=attrs,
                    integration=Integration.LANGCHAIN,
                    kind=trace.SpanKind.CLIENT
                    if kind in ("chat", "text_completion")
                    else trace.SpanKind.INTERNAL,
                )
            finally:
                context.detach(token)
            run = Run(op, kind)
            with self.lock:
                closed = self.closed
                if not closed:
                    self.runs[run_id] = run
            if closed:
                op.end()
                return
            safe(extraction.request, op, kind, value, options)
            from .execution import bind

            safe(bind, self, run_id, run)
        except Exception:
            with self.lock:
                self.claimed.discard(run_id)
            raise

    def finish(self, run_id, value=None, error=None):
        with self.lock:
            run = self.runs.pop(run_id, None)
            self.claimed.discard(run_id)
        if run is not None:
            try:
                if error is None:
                    safe(extraction.response, run.operation, run.kind, value)
            finally:
                run.operation.end(
                    None if isinstance(error, self.control_flow) else error
                )

    def close(self):
        with self.lock:
            self.closed = True
            pending = tuple(self.runs.values())
            self.runs.clear()
            self.claimed.clear()
        for run in pending:
            safe(run.operation.end)

    def on_chain_start(
        self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs
    ):
        kind = "invoke_agent" if kwargs.get("run_type") == "agent" else "chain"
        safe(self.start, kind, serialized, inputs, run_id, parent_run_id, **kwargs)

    def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, **kwargs
    ):
        safe(self.start, "chat", serialized, messages, run_id, parent_run_id, **kwargs)

    def on_llm_start(
        self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs
    ):
        safe(
            self.start,
            "text_completion",
            serialized,
            prompts,
            run_id,
            parent_run_id,
            **kwargs,
        )

    def on_tool_start(
        self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs
    ):
        safe(
            self.start,
            "execute_tool",
            serialized,
            kwargs["inputs"] if kwargs.get("inputs") is not None else input_str,
            run_id,
            parent_run_id,
            **kwargs,
        )

    def on_retriever_start(
        self, serialized, query, *, run_id, parent_run_id=None, **kwargs
    ):
        safe(
            self.start, "retriever", serialized, query, run_id, parent_run_id, **kwargs
        )

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        safe(self.finish, run_id, outputs)

    def on_llm_end(self, response, *, run_id, **kwargs):
        safe(self.finish, run_id, response)

    def on_tool_end(self, output, *, run_id, **kwargs):
        safe(self.finish, run_id, output)

    def on_retriever_end(self, documents, *, run_id, **kwargs):
        safe(self.finish, run_id, documents)

    def on_chain_error(self, error, *, run_id, **kwargs):
        safe(self.finish, run_id, error=error)

    on_llm_error = on_chain_error
    on_tool_error = on_chain_error
    on_retriever_error = on_chain_error
