"""OpenAI Chat Completions and Responses streaming events."""

from ..._core.safety import safe
from .._shared.accumulation import Accumulator as BaseAccumulator
from .._shared.extraction import get, sequence
from .openai_extraction import finish_reason, response


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
        if type(chunk) is str:
            self.append(first, "text", chunk)
        elif type(delta) is str:
            if get(chunk, "type") == "response.function_call_arguments.delta":
                tool = self.tool(first, get(chunk, "output_index", 0))
                if tool is not None:
                    self.append(tool, "arguments", delta)
            elif get(chunk, "type") == "response.output_text.delta":
                self.append(first, "text", delta)
        else:
            self.append(first, "text", get(delta, "text"))
        reason = finish_reason(get(delta, "stop_reason"))
        if reason:
            first["finish_reason"] = reason
        for choice in sequence(get(chunk, "choices")):
            candidate = self.candidate(get(choice, "index", 0))
            if candidate is None:
                continue
            reason = finish_reason(get(choice, "finish_reason"))
            if reason:
                candidate["finish_reason"] = reason
            change = get(choice, "delta", {})
            self.append(candidate, "text", get(change, "content"))
            for item in sequence(get(change, "tool_calls"), 32):
                tool = self.tool(candidate, get(item, "index", 0))
                if tool is None:
                    continue
                fn = get(item, "function", {})
                for field, value in (
                    ("id", get(item, "id")),
                    ("name", get(fn, "name")),
                    ("arguments", get(fn, "arguments")),
                ):
                    self.append(tool, field, value)
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
        self.emit(op)
