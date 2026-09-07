"""Install langchain and langchain-openai; configure OpenAI and Confident keys."""

import asyncio
import os

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI

import confident_trace as ct


@tool
def lookup(value: str) -> str:
    """Look up the uppercase form of a value."""
    with ct.span("lookup-storage"):
        return value.upper()


async def main():
    ct.init()
    try:
        agent = create_agent(
            model=ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
            tools=[lookup],
            system_prompt="Use lookup when asked; report its result concisely.",
        )
        with ct.span("request", thread_id="example-conversation"):
            result = await agent.ainvoke(
                {"messages": [{"role": "user", "content": "Use lookup on hello."}]}
            )
            print(result["messages"][-1].content)
    finally:
        ct.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
