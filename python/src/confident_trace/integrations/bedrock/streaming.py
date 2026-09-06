"""Bedrock event stream lifecycle and Converse delta parsing."""

import weakref

import wrapt

from ..._core.safety import safe
from ..._semconv import genai_v1_37_0 as ai
from .._shared.accumulation import Accumulator as BaseAccumulator
from .._shared.extraction import get
from .extraction import finish_reason, usage


class EventStream(wrapt.ObjectProxy):
    """Preserve Botocore's iterable (not iterator) and explicit-close interface."""

    def __init__(self, wrapped, op):
        super().__init__(wrapped)
        self._self_op = op
        self._self_consume = Accumulator()
        self._self_finalizer = weakref.finalize(self, op.end)

    def __iter__(self):
        op = self._self_op
        with op.active():
            iterator = iter(self.__wrapped__)
        while True:
            try:
                with op.active():
                    event = next(iterator)
            except StopIteration:
                op.end()
                return
            except BaseException as error:
                op.end(error)
                raise
            safe(self._self_consume, op, event)
            yield event

    def close(self):
        try:
            with self._self_op.active():
                return self.__wrapped__.close()
        finally:
            self._self_op.end()


class Accumulator(BaseAccumulator):
    def __call__(self, op, event):
        if op.ended or not op.span.is_recording():
            return
        usage(op, get(get(event, "metadata"), "usage"))
        reason = finish_reason(get(get(event, "messageStop"), "stopReason"))
        if reason:
            safe(op.span.set_attribute, ai.GEN_AI_RESPONSE_FINISH_REASONS, [reason])
        if not self.ready(op):
            return
        candidate = self.candidate()
        if reason:
            candidate["finish_reason"] = reason
        start = get(event, "contentBlockStart")
        tool_data = get(get(start, "start"), "toolUse")
        if tool_data is not None:
            tool = self.tool(candidate, get(start, "contentBlockIndex", 0))
            if tool is not None:
                self.append(tool, "id", get(tool_data, "toolUseId"))
                self.append(tool, "name", get(tool_data, "name"))
        block = get(event, "contentBlockDelta")
        delta = get(block, "delta", {})
        self.append(candidate, "text", get(delta, "text"))
        fragment = get(get(delta, "toolUse"), "input")
        if type(fragment) is str:
            tool = self.tool(candidate, get(block, "contentBlockIndex", 0))
            if tool is not None:
                self.append(tool, "arguments", fragment)
        self.emit(op)
