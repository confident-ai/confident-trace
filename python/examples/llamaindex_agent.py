"""Install llama-index-core and llama-index-llms-openai; set OPENAI_API_KEY."""

import asyncio
import os

from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.tools import FunctionTool
from llama_index.llms.openai import OpenAI

import confident_trace as ct


def lookup(value: str) -> str:
    """Look up a value."""
    with ct.span("lookup-storage"):
        return value.upper()


async def main():
    ct.init()
    try:
        agent = FunctionAgent(
            name="assistant",
            llm=OpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
            tools=[FunctionTool.from_defaults(lookup)],
        )
        with ct.span("request", thread_id="example-conversation"):
            print(await agent.run("Use lookup on hello."))
    finally:
        ct.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
