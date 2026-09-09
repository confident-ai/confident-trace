"""Public structured tracing fields."""

from typing import TypedDict


class _ThreadOptions(TypedDict, total=False):
    tags: list[str]
    metadata: dict[str, object] | None


class ThreadFields(_ThreadOptions, total=False):
    id: str


class _ThreadWithId(_ThreadOptions):
    id: str
