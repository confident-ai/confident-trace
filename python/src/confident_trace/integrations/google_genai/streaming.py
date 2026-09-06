"""Google GenAI candidate and function-call stream parsing."""

from ..._core import runtime as _runtime
from ..._core.safety import safe
from .._shared.accumulation import Accumulator as BaseAccumulator
from .._shared.extraction import get, sequence
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
        self.candidate()
        rt = _runtime.current()
        for item in sequence(get(chunk, "candidates")):
            candidate = self.candidate(get(item, "index", 0) or 0)
            if candidate is None:
                continue
            reason = finish_reason(get(item, "finish_reason"))
            if reason:
                candidate["finish_reason"] = reason
            for part in sequence(get(get(item, "content", {}), "parts")):
                self.append(candidate, "text", get(part, "text"))
                fn = get(part, "function_call")
                if fn:
                    tool = self.tool(candidate, get(fn, "id") or get(fn, "name", ""))
                    if tool is not None and not tool["name"]:
                        self.append(tool, "name", get(fn, "name"))
                        self.append(tool, "id", get(fn, "id"))
                        encoded = rt.policy.encode(get(fn, "args"))
                        self.append(tool, "arguments", encoded)
        self.emit(op)
