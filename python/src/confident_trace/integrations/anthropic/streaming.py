"""Anthropic event parsing, lazy managers, and message-stream helpers."""

import wrapt

from ..._core.safety import safe
from .._shared.accumulation import Accumulator as BaseAccumulator
from .._shared.extraction import get
from .._shared.streams import AsyncStream as BaseAsyncStream
from .._shared.streams import Stream as BaseStream
from .extraction import finish_reason, response


class Accumulator(BaseAccumulator):
    def __call__(self, op, chunk):
        if op.ended or not op.span.is_recording():
            return
        value = get(chunk, "response", get(chunk, "message", chunk))
        safe(response, op, value)
        if not self.ready(op):
            return
        if get(value, "output") or get(value, "content"):
            return
        first = self.candidate()
        delta = get(chunk, "delta", {})
        self.append(first, "text", chunk if type(chunk) is str else get(delta, "text"))
        reason = finish_reason(get(delta, "stop_reason"))
        if reason:
            first["finish_reason"] = reason
        block = get(chunk, "content_block", get(chunk, "item"))
        if get(block, "type") == "text":
            self.append(first, "text", get(block, "text"))
        if get(block, "type") in ("tool_use", "function_call"):
            tool = self.tool(first, get(chunk, "index", get(chunk, "output_index", 0)))
            if tool is not None:
                # 'done' snapshots are not additional fragments.
                if not tool["name"]:
                    self.append(tool, "name", get(block, "name"))
                    self.append(tool, "id", get(block, "call_id", get(block, "id")))
        fragment = get(delta, "partial_json")
        if type(fragment) is str:
            tool = self.tool(first, get(chunk, "index", 0))
            if tool is not None:
                self.append(tool, "arguments", fragment)
        self.emit(op)


class Stream(BaseStream):
    @property
    def text_stream(self):
        from .._shared.extraction import get

        def texts():
            for event in self:
                delta = get(event, "delta", {})
                text = get(delta, "text")
                if type(text) is str:
                    yield text

        return texts()

    def get_final_message(self):
        from .extraction import response

        value = self._drive(self.__wrapped__.get_final_message)
        safe(response, self._operation(), value)
        return value


class AsyncStream(BaseAsyncStream):
    @property
    def text_stream(self):
        from .._shared.extraction import get

        async def texts():
            async for event in self:
                delta = get(event, "delta", {})
                text = get(delta, "text")
                if type(text) is str:
                    yield text

        return texts()

    async def get_final_message(self):
        from .extraction import response

        value = await self._adrive(self.__wrapped__.get_final_message)
        safe(response, self._operation(), value)
        return value


class Manager(wrapt.ObjectProxy):
    """Anthropic's lazy stream manager starts the request on entry."""

    def __init__(self, wrapped, kwargs, instance):
        super().__init__(wrapped)
        self._self_kwargs = kwargs
        self._self_instance = instance
        self._self_op = None

    def __enter__(self):
        op = self._begin()
        self._self_op = op
        try:
            with op.active():
                value = self.__wrapped__.__enter__()
            return Stream(value, operation=op, consume=Accumulator())
        except BaseException as error:
            op.end(error)
            raise

    def __exit__(self, *args):
        try:
            with self._self_op.active():
                return self.__wrapped__.__exit__(*args)
        finally:
            self._self_op.end(args[1])

    async def __aenter__(self):
        op = self._begin()
        self._self_op = op
        try:
            with op.active():
                value = await self.__wrapped__.__aenter__()
            return AsyncStream(value, operation=op, consume=Accumulator())
        except BaseException as error:
            op.end(error)
            raise

    async def __aexit__(self, *args):
        try:
            with self._self_op.active():
                return await self.__wrapped__.__aexit__(*args)
        finally:
            self._self_op.end(args[1])

    def _begin(self):
        from .instrumentation import begin

        return begin(self._self_kwargs, self._self_instance)
