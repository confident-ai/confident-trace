"""Bounded accumulation of normalized text, tool calls, and completion reasons."""

from ... import _attributes as confident
from ..._core.safety import safe
from ..._core.spans import content
from ..._semconv import genai_v1_37_0 as ai
from .extraction import arguments


class Accumulator:
    def __init__(self):
        self.candidates = {}
        self.remaining = None
        self.truncated = False

    def ready(self, op):
        rt = op.runtime
        if op.ended or not op.span.is_recording() or not rt or not rt.policy.enabled:
            return False
        if self.remaining is None:
            self.remaining = rt.policy.max_bytes // 4
        return True

    def append(self, record, field, value):
        if type(value) is not str:
            return
        size = max(0, self.remaining)
        fragment = value[:size]
        self.remaining -= len(fragment)
        self.truncated |= len(value) > size
        record[field] = record.get(field, "") + fragment

    def candidate(self, index=0):
        if type(index) is not int or index < 0 or index >= 128:
            return None
        return self.candidates.setdefault(
            index, {"text": "", "tools": {}, "finish_reason": ""}
        )

    def tool(self, candidate, index):
        if (
            type(index) not in (int, str)
            or len(candidate["tools"]) >= 32
            and index not in candidate["tools"]
        ):
            self.truncated = True
            return None
        if type(index) is str:
            index = index[:256]
        return candidate["tools"].setdefault(
            index, {"type": "tool_call", "id": "", "name": "", "arguments": ""}
        )

    def emit(self, op):
        output = []
        for _, candidate in sorted(self.candidates.items()):
            blocks = (
                [{"type": "text", "content": candidate["text"]}]
                if candidate["text"]
                else []
            )
            blocks += [
                {**tool, "arguments": arguments(tool["arguments"])}
                for tool in candidate["tools"].values()
            ]
            if blocks:
                output.append(
                    {
                        "role": "assistant",
                        "parts": blocks,
                        "finish_reason": candidate["finish_reason"],
                    }
                )
        reasons = [
            candidate["finish_reason"]
            for _, candidate in sorted(self.candidates.items())
            if candidate["finish_reason"]
        ]
        if reasons:
            safe(op.span.set_attribute, ai.GEN_AI_RESPONSE_FINISH_REASONS, reasons)
        if output:
            content(op.span, ai.GEN_AI_OUTPUT_MESSAGES, output)
            if op.is_entry:
                content(op.span, confident.TRACE_OUTPUT, output)
        if self.truncated:
            safe(op.span.set_attribute, confident.SPAN_CONTENT_TRUNCATED, True)
