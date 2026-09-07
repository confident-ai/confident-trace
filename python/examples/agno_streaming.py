"""Install agno and openai; configure OpenAI and Confident keys."""

import asyncio
import os

from agno.agent import Agent
from agno.models.openai import OpenAIChat

import confident_trace as ct


async def main():
    ct.init()
    try:
        agent = Agent(
            name="assistant",
            model=OpenAIChat(id=os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
        )
        with ct.span("request", thread_id="example-conversation"):
            stream = agent.arun("Explain OpenTelemetry in one sentence.", stream=True)
            try:
                async for event in stream:
                    if event.content:
                        print(event.content, end="", flush=True)
                print()
            finally:
                await stream.aclose()
    finally:
        ct.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
