"""Stream proxies activate OTel context only while driving the underlying stream."""

from __future__ import annotations

import weakref

import wrapt

from ._spans import safe


class Stream(wrapt.ObjectProxy):
    def __init__(self, wrapped, *, operation=None, factory=None, consume=None):
        super().__init__(wrapped)
        self._self_op = operation
        self._self_factory = factory
        self._self_consume = consume
        self._self_finalizer = (
            weakref.finalize(self, operation.end) if operation else None
        )

    def _operation(self):
        if self._self_op is None:
            self._self_op = self._self_factory()
            self._self_finalizer = weakref.finalize(self, self._self_op.end)
        return self._self_op

    def _drive(self, method, *args):
        op = self._operation()
        try:
            with op.active():
                value = method(*args)
            if self._self_consume:
                safe(self._self_consume, op, value)
            return value
        except StopIteration as end:
            if (
                self._self_factory
                and getattr(op, "capture_result", True)
                and end.value is not None
            ):
                safe(op.output, end.value)
            op.end()
            raise
        except BaseException as error:
            op.end(error)
            raise

    @property
    def text_stream(self):
        from ._extract import get

        def texts():
            for event in self:
                delta = get(event, "delta", {})
                text = get(delta, "text")
                if type(text) is str:
                    yield text

        return texts()

    def get_final_message(self):
        from ._extract import response

        value = self._drive(self.__wrapped__.get_final_message)
        safe(response, self._operation(), value, "anthropic")
        return value

    def __iter__(self):
        return self

    def __next__(self):
        return self._drive(self.__wrapped__.__next__)

    def send(self, value):
        return self._drive(self.__wrapped__.send, value)

    def throw(self, *args):
        return self._drive(self.__wrapped__.throw, *args)

    def close(self):
        try:
            return self._drive(self.__wrapped__.close)
        finally:
            self._operation().end()

    def __enter__(self):
        self._drive(self.__wrapped__.__enter__)
        return self

    def __exit__(self, *args):
        try:
            return self._drive(self.__wrapped__.__exit__, *args)
        finally:
            self._operation().end(args[1])


class AsyncStream(Stream):
    async def _adrive(self, method, *args):
        op = self._operation()
        try:
            with op.active():
                value = await method(*args)
            if self._self_consume:
                safe(self._self_consume, op, value)
            return value
        except StopAsyncIteration:
            op.end()
            raise
        except BaseException as error:
            op.end(error)
            raise

    @property
    def text_stream(self):
        from ._extract import get

        async def texts():
            async for event in self:
                delta = get(event, "delta", {})
                text = get(delta, "text")
                if type(text) is str:
                    yield text

        return texts()

    async def get_final_message(self):
        from ._extract import response

        value = await self._adrive(self.__wrapped__.get_final_message)
        safe(response, self._operation(), value, "anthropic")
        return value

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._adrive(self.__wrapped__.__anext__)

    async def asend(self, value):
        return await self._adrive(self.__wrapped__.asend, value)

    async def athrow(self, *args):
        return await self._adrive(self.__wrapped__.athrow, *args)

    async def aclose(self):
        try:
            method = getattr(self.__wrapped__, "aclose", None) or self.__wrapped__.close
            return await self._adrive(method)
        finally:
            self._operation().end()

    async def close(self):
        return await self.aclose()

    async def __aenter__(self):
        await self._adrive(self.__wrapped__.__aenter__)
        return self

    async def __aexit__(self, *args):
        try:
            return await self._adrive(self.__wrapped__.__aexit__, *args)
        finally:
            self._operation().end(args[1])
