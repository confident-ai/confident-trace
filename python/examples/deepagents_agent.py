"""Python 3.11+: install deepagents and langchain-openai; configure API keys."""

import asyncio
import os

from deepagents import create_deep_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

import confident_trace as ct


@tool
def lookup(topic: str) -> str:
    """Retrieve a local note about tracing."""
    with ct.span("lookup-notes"):
        return f"{topic}: callback run IDs preserve the agent and tool hierarchy."


async def main():
    ct.init()
    try:
        model = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        agent = create_deep_agent(
            model=model,
            name="deep-research",
            system_prompt="Delegate the research to researcher, then summarize its answer.",
            subagents=[
                {
                    "name": "researcher",
                    "description": "Research tracing using local notes.",
                    "system_prompt": "Use lookup to find evidence for your answer.",
                    "model": model,
                    "tools": [lookup],
                }
            ],
        )
        with ct.span("request", thread_id="deepagents-example"):
            result = await agent.ainvoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": "Research how agent tracing preserves tool hierarchy.",
                        }
                    ]
                }
            )
            print(result["messages"][-1].content)
    finally:
        ct.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
