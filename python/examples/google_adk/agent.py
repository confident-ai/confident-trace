"""pip install -e './python[google-adk]'; set GOOGLE_API_KEY and CONFIDENT_API_KEY."""

import asyncio
import os
from contextlib import aclosing

from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import InMemoryRunner
from google.genai import types

from confident_trace import init, shutdown


def city_info(city: str) -> str:
    """Return an example fact about a city."""
    return f"Requested city: {city}"


async def main():
    init()
    runner = InMemoryRunner(
        app_name="confident-adk",
        agent=LlmAgent(
            name="assistant",
            model=os.environ.get("GOOGLE_MODEL", "gemini-2.5-flash"),
            instruction="Use city_info when asked about a city.",
            tools=[city_info],
        ),
    )
    session = await runner.session_service.create_session(
        app_name="confident-adk", user_id="example-user"
    )
    try:
        async with aclosing(
            runner.run_async(
                user_id="example-user",
                session_id=session.id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text="Tell me about Macau")]
                ),
                run_config=RunConfig(streaming_mode=StreamingMode.SSE),
            )
        ) as events:
            async for event in events:
                if event.content:
                    for part in event.content.parts or []:
                        if part.text:
                            print(part.text, end="", flush=True)
        print()
    finally:
        shutdown()


if __name__ == "__main__":
    asyncio.run(main())
