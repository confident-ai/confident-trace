"""Bounded, side-effect-free serialization of application values."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from itertools import islice
from typing import Any


@dataclass(frozen=True)
class ContentPolicy:
    enabled: bool = True
    max_bytes: int = 16384
    redact: Callable[[Any], Any] | None = None

    def encode(self, value: Any, *, shape: str | None = None) -> str | None:
        if not self.enabled:
            return None
        try:
            value = self.redact(value) if self.redact else value
            remaining = [min(self.max_bytes, 1024)]

            def clean(item, depth=0):
                remaining[0] -= 1
                if remaining[0] < 0 or depth > 8:
                    return "[truncated]"
                if item is None or type(item) in (bool, int):
                    return item
                if type(item) is float:
                    return item if math.isfinite(item) else None
                if type(item) is str:
                    return item[: self.max_bytes]
                if type(item) in (list, tuple):
                    return [
                        clean(v, depth + 1) for v in islice(item, max(0, remaining[0]))
                    ]
                if type(item) is dict:
                    return {
                        k[:256]: clean(v, depth + 1)
                        for k, v in islice(item.items(), max(0, remaining[0]))
                        if type(k) is str
                    }
                return "[unsupported]"

            value = clean(value)
            if shape:
                if not valid_content(value, shape):
                    return None
                return bounded_messages(value, shape, self.max_bytes)
            result = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            if len(result.encode()) > self.max_bytes:
                return '"[truncated]"'
            return result
        except Exception:
            # A failing redactor must never leak the unredacted value.
            return None


def valid_content(value, shape):
    """Validate package-owned shapes after redaction; never validate third-party spans.

    Generic part types and roles remain extensible, as in the upstream schemas.
    This bounded structural check avoids a runtime JSON Schema dependency.
    """
    if type(value) is not list:
        return False
    if shape == "system-instructions":
        blocks = value
    else:
        blocks = []
        for message in value:
            if (
                type(message) is not dict
                or type(message.get("role")) is not str
                or type(message.get("parts")) is not list
            ):
                return False
            if (
                shape == "output-messages"
                and type(message.get("finish_reason")) is not str
            ):
                return False
            blocks.extend(message["parts"])
    for part in blocks:
        if type(part) is not dict or type(part.get("type")) is not str:
            return False
        kind = part["type"]
        if kind == "text" and type(part.get("content")) is not str:
            return False
        if kind == "tool_call" and type(part.get("name")) is not str:
            return False
        if kind == "tool_call_response" and "response" not in part:
            return False
        if (
            kind in ("tool_call", "tool_call_response")
            and part.get("id") is not None
            and type(part["id"]) is not str
        ):
            return False
    return True


def bounded_messages(value, shape, limit):
    """Keep a valid prefix; shorten text or remove whole parts/messages."""
    while True:
        result = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        if len(result) <= limit:  # ensure_ascii makes character count byte count.
            return result
        if not value:
            return None
        blocks = value if shape == "system-instructions" else value[-1]["parts"]
        if blocks:
            part = blocks[-1]
            text = part.get("content") if part.get("type") == "text" else None
            if type(text) is str and len(text) > 16:
                part["content"] = text[: len(text) // 2] + "…"
            else:
                blocks.pop()
        else:
            value.pop()
