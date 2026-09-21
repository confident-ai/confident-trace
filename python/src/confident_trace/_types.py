"""Public structured tracing fields."""

from typing import TypedDict


class _ThreadOptions(TypedDict, total=False):
    tags: list[str]
    metadata: dict[str, object] | None


class ThreadFields(_ThreadOptions, total=False):
    id: str


class _ThreadWithId(_ThreadOptions):
    id: str


class CustomerFields(TypedDict, total=False):
    """The B2B account an end user belongs to."""

    id: str
    name: str


class UserFields(TypedDict, total=False):
    """The end user a trace belongs to."""

    id: str
    name: str
