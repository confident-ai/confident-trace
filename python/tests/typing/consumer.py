from collections.abc import AsyncIterator, Iterator

from confident_trace import (
    project_context,
    span,
    suppress_tracing,
    turn,
    update_llm_span,
    update_span,
    update_trace,
)


@span
def plain(value: int) -> str:
    return str(value)


@span(type="tool", name="lookup")
def decorated(value: int, *, label: str = "") -> str:
    return label + str(value)


@span(type="agent")
async def asynchronous(value: int) -> str:
    return str(value)


@span()
def generate(value: int) -> Iterator[str]:
    yield str(value)


@span()
async def async_generate(value: int) -> AsyncIterator[str]:
    yield str(value)


result: str = decorated(1, label="ok")
plain_result: str = plain(1)
iterator: Iterator[str] = generate(1)
async_iterator: AsyncIterator[str] = async_generate(1)
with span("manual", type="llm", model="test", input_token_count=0) as current:
    current.set_attribute("key", "value")
    update_span(output=None, metadata={"count": 1})
    update_trace(thread={"id": "chat", "tags": ["support"]})
    update_llm_span(input_token_count=1, cost_per_input_token=0.01)
with turn(thread_id="chat"), project_context(api_key="key"), suppress_tracing():
    pass


async def scopes() -> None:
    result: str = await asynchronous(1)
    async with turn(thread={"id": "chat"}), project_context(api_key="key"), suppress_tracing():
        async with span(type="agent") as current:
            current.set_attribute("result", result)


span(anything=True)  # type: ignore[call-overload]
span(type="workflow")  # type: ignore[call-overload]
span(input_token_count="five")  # type: ignore[call-overload]
update_span(anything=True)  # type: ignore[call-arg]
update_trace(test_case_id=12)  # type: ignore[arg-type]
update_trace(thread={"unknown": "value"})  # type: ignore[typeddict-unknown-key]
update_llm_span(input_token_count="five")  # type: ignore[arg-type]
turn(anything=True)  # type: ignore[call-overload]
project_context(api_key=123)  # type: ignore[arg-type]
suppress_tracing(anything=True)  # type: ignore[call-arg]
decorated("bad")  # type: ignore[arg-type]
wrong_result: int = decorated(1)  # type: ignore[assignment]
plain("bad")  # type: ignore[arg-type]
generate("bad")  # type: ignore[arg-type]
async_generate("bad")  # type: ignore[arg-type]

span(type="tool", model="test")  # type: ignore[call-overload]

turn()  # type: ignore[call-overload]
turn(thread={"tags": ["support"]})  # type: ignore[call-overload]

update_span(output="answer", model="test", input_token_count=0)
update_span(input_token_count="five")  # type: ignore[arg-type]
