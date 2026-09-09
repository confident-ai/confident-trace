"""Public IDE signatures; omitted update fields retain their existing values."""

from collections.abc import Callable
from types import TracebackType
from typing import Literal, ParamSpec, TypeVar, overload

from opentelemetry.trace import Span, SpanContext
from opentelemetry.util.types import Attributes

from ._attributes import Integration as Integration
from ._bootstrap import init as init
from ._core.runtime import SEMCONV_VERSION as SEMCONV_VERSION
from ._types import ThreadFields as ThreadFields
from ._types import _ThreadWithId

SpanType = Literal["agent", "llm", "retriever", "tool", "custom"]
_P = ParamSpec("_P")
_R = TypeVar("_R")

class _SpanScope:
    def __call__(self, fn: Callable[_P, _R]) -> Callable[_P, _R]: ...
    def __enter__(self) -> Span: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...
    async def __aenter__(self) -> Span: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

class _RequestScope:
    def __enter__(self) -> _RequestScope: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    async def __aenter__(self) -> _RequestScope: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

@overload
def span(
    func: Callable[_P, _R],
    *,
    name: str | None = ...,
    type: SpanType | None = ...,
    kind: Literal["step", "tool"] | None = ...,
    capture_content: bool = ...,
    attributes: Attributes = ...,
    input: object = ...,
    output: object = ...,
    metadata: dict[str, object] | None = ...,
    retrieval_context: list[str] | None = ...,
    context: list[str] | None = ...,
    expected_output: object = ...,
    tools_called: list[dict[str, object]] | None = ...,
    expected_tools: list[dict[str, object]] | None = ...,
    tags: list[str] = ...,
    environment: str = ...,
    user_id: str = ...,
    thread_id: str = ...,
    turn_id: str = ...,
    test_case_id: str = ...,
    thread: ThreadFields = ...,
) -> Callable[_P, _R]: ...
@overload
def span(
    func: str | None = ...,
    *,
    name: str | None = ...,
    type: SpanType | None = ...,
    kind: Literal["step", "tool"] | None = ...,
    capture_content: bool = ...,
    attributes: Attributes = ...,
    input: object = ...,
    output: object = ...,
    metadata: dict[str, object] | None = ...,
    retrieval_context: list[str] | None = ...,
    context: list[str] | None = ...,
    expected_output: object = ...,
    tools_called: list[dict[str, object]] | None = ...,
    expected_tools: list[dict[str, object]] | None = ...,
    tags: list[str] = ...,
    environment: str = ...,
    user_id: str = ...,
    thread_id: str = ...,
    turn_id: str = ...,
    test_case_id: str = ...,
    thread: ThreadFields = ...,
) -> _SpanScope: ...
@overload
def span(
    func: Callable[_P, _R],
    *,
    name: str | None = ...,
    type: Literal["llm"],
    kind: None = ...,
    capture_content: bool = ...,
    attributes: Attributes = ...,
    input: object = ...,
    output: object = ...,
    metadata: dict[str, object] | None = ...,
    retrieval_context: list[str] | None = ...,
    context: list[str] | None = ...,
    expected_output: object = ...,
    tools_called: list[dict[str, object]] | None = ...,
    expected_tools: list[dict[str, object]] | None = ...,
    tags: list[str] = ...,
    environment: str = ...,
    user_id: str = ...,
    thread_id: str = ...,
    turn_id: str = ...,
    test_case_id: str = ...,
    thread: ThreadFields = ...,
    model: str = ...,
    provider: str = ...,
    input_token_count: int = ...,
    output_token_count: int = ...,
    cost_per_input_token: float = ...,
    cost_per_output_token: float = ...,
) -> Callable[_P, _R]: ...
@overload
def span(
    func: str | None = ...,
    *,
    name: str | None = ...,
    type: Literal["llm"],
    kind: None = ...,
    capture_content: bool = ...,
    attributes: Attributes = ...,
    input: object = ...,
    output: object = ...,
    metadata: dict[str, object] | None = ...,
    retrieval_context: list[str] | None = ...,
    context: list[str] | None = ...,
    expected_output: object = ...,
    tools_called: list[dict[str, object]] | None = ...,
    expected_tools: list[dict[str, object]] | None = ...,
    tags: list[str] = ...,
    environment: str = ...,
    user_id: str = ...,
    thread_id: str = ...,
    turn_id: str = ...,
    test_case_id: str = ...,
    thread: ThreadFields = ...,
    model: str = ...,
    provider: str = ...,
    input_token_count: int = ...,
    output_token_count: int = ...,
    cost_per_input_token: float = ...,
    cost_per_output_token: float = ...,
) -> _SpanScope: ...
def update_span(
    *,
    name: str = ...,
    input: object = ...,
    output: object = ...,
    metadata: dict[str, object] | None = ...,
    retrieval_context: list[str] | None = ...,
    context: list[str] | None = ...,
    expected_output: object = ...,
    tools_called: list[dict[str, object]] | None = ...,
    expected_tools: list[dict[str, object]] | None = ...,
    model: str = ...,
    provider: str = ...,
    input_token_count: int = ...,
    output_token_count: int = ...,
    cost_per_input_token: float = ...,
    cost_per_output_token: float = ...,
) -> None: ...
def update_trace(
    *,
    name: str = ...,
    input: object = ...,
    output: object = ...,
    metadata: dict[str, object] | None = ...,
    retrieval_context: list[str] | None = ...,
    context: list[str] | None = ...,
    expected_output: object = ...,
    tools_called: list[dict[str, object]] | None = ...,
    expected_tools: list[dict[str, object]] | None = ...,
    tags: list[str] = ...,
    environment: str = ...,
    user_id: str = ...,
    thread_id: str = ...,
    turn_id: str = ...,
    test_case_id: str = ...,
    thread: ThreadFields = ...,
) -> None: ...
def update_llm_span(
    *,
    model: str = ...,
    provider: str = ...,
    input_token_count: int = ...,
    output_token_count: int = ...,
    cost_per_input_token: float = ...,
    cost_per_output_token: float = ...,
) -> None: ...
@overload
def turn(
    name: str = ...,
    *,
    previous: SpanContext | None = ...,
    input: object = ...,
    output: object = ...,
    metadata: dict[str, object] | None = ...,
    retrieval_context: list[str] | None = ...,
    context: list[str] | None = ...,
    expected_output: object = ...,
    tools_called: list[dict[str, object]] | None = ...,
    expected_tools: list[dict[str, object]] | None = ...,
    tags: list[str] = ...,
    environment: str = ...,
    user_id: str = ...,
    thread_id: str,
    turn_id: str = ...,
    test_case_id: str = ...,
    thread: ThreadFields = ...,
) -> _SpanScope: ...
@overload
def turn(
    name: str = ...,
    *,
    previous: SpanContext | None = ...,
    input: object = ...,
    output: object = ...,
    metadata: dict[str, object] | None = ...,
    retrieval_context: list[str] | None = ...,
    context: list[str] | None = ...,
    expected_output: object = ...,
    tools_called: list[dict[str, object]] | None = ...,
    expected_tools: list[dict[str, object]] | None = ...,
    tags: list[str] = ...,
    environment: str = ...,
    user_id: str = ...,
    thread_id: str = ...,
    turn_id: str = ...,
    test_case_id: str = ...,
    thread: _ThreadWithId,
) -> _SpanScope: ...
def project(*, api_key: str) -> _RequestScope: ...
def suppress_tracing() -> _RequestScope: ...
def flush(timeout_millis: int = ...) -> bool: ...
def shutdown(timeout_millis: int = ...) -> bool: ...
