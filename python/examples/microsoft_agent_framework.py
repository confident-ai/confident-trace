"""OPENAI_API_KEY; pip install confident-trace agent-framework-core agent-framework-openai."""

import asyncio
import os

from agent_framework import Agent, AgentSession
from agent_framework.openai import OpenAIChatClient

from confident_trace import init, shutdown, span, update_trace


async def main():
    init()
    try:
        async with Agent(
            client=OpenAIChatClient(model=os.environ["OPENAI_MODEL"]),
            name="assistant",
            instructions="Answer concisely.",
        ) as agent:
            with span("request"):
                update_trace(thread_id="example-session")
                result = await agent.run(
                    "What is OpenTelemetry?",
                    session=AgentSession(session_id="example-session"),
                )
                print(result.text)
    finally:
        shutdown()


if __name__ == "__main__":
    asyncio.run(main())
