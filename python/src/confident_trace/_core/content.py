"""Bounded, side-effect-free serialization of application values."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from itertools import islice
from typing import Any
from weakref import WeakKeyDictionary

from .media import Media

MEDIA_TYPES = ("blob", "uri")
MEDIA_OVERHEAD = 256


class MediaBudget:
    __slots__ = ("per_item", "remaining")

    def __init__(self, per_item, per_attribute):
        self.per_item = per_item
        self.remaining = per_attribute

    @classmethod
    def spent(cls):
        return cls(0, 0)

    def part(self, media):
        part = media.to_part(min(self.per_item, self.remaining))
        if "content" in part:
            self.remaining -= media.byte_size() or 0
        return part


_SPAN_BUDGETS: WeakKeyDictionary = WeakKeyDictionary()


def span_budget(span, policy, shape):
    """The budget this span shares with every other attribute it writes."""
    if not shape:
        return MediaBudget.spent()
    budget = _SPAN_BUDGETS.get(span)
    if budget is None:
        budget = policy.budget(shape)
        _SPAN_BUDGETS[span] = budget
    return budget


@dataclass(frozen=True)
class ContentPolicy:
    enabled: bool = True
    max_bytes: int = 16384
    redact: Callable[[Any], Any] | None = None
    max_media_bytes: int = 5242880
    max_media_total_bytes: int = 16777216

    def budget(self, shape) -> MediaBudget:
        if not shape:
            return MediaBudget.spent()
        return MediaBudget(self.max_media_bytes, self.max_media_total_bytes)

    def encode(
        self,
        value: Any,
        *,
        shape: str | None = None,
        budget: MediaBudget | None = None,
    ) -> str | None:
        if not self.enabled:
            return None
        try:
            value = self.redact(value) if self.redact else value
            remaining = [min(self.max_bytes, 1024)]
            budget = self.budget(shape) if budget is None else budget

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
                if type(item) is Media:
                    return budget.part(item)
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
            limit = self.max_bytes + media_length(value)
            if shape:
                if not valid_content(value, shape):
                    return None
                return bounded_messages(value, shape, limit)
            result = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            if len(result.encode()) > limit:
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


def media_length(value, depth=0):
    if depth > 8:
        return 0
    if type(value) is dict:
        if value.get("type") in MEDIA_TYPES:
            payload = value.get("content") or value.get("uri")
            return (len(payload) if type(payload) is str else 0) + MEDIA_OVERHEAD
        return sum(media_length(v, depth + 1) for v in value.values())
    if type(value) in (list, tuple):
        return sum(media_length(v, depth + 1) for v in value)
    return 0


def trimmable(blocks):
    for index in range(len(blocks) - 1, -1, -1):
        part = blocks[index]
        if type(part) is not dict or part.get("type") not in MEDIA_TYPES:
            return index
    return None


def bounded_messages(value, shape, limit):
    """Keep a valid prefix; shorten text or remove whole parts/messages."""
    while True:
        result = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        if len(result) <= limit:  # ensure_ascii makes character count byte count.
            return result
        if not value:
            return None
        blocks = value if shape == "system-instructions" else value[-1]["parts"]
        # Media is already within budget, so text yields first.
        index = trimmable(blocks)
        if index is None:
            value.pop()
            continue
        part = blocks[index]
        text = part.get("content") if part.get("type") == "text" else None
        if type(text) is str and len(text) > 16:
            part["content"] = text[: len(text) // 2] + "…"
        else:
            blocks.pop(index)
