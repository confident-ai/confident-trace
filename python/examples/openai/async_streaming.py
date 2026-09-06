"""Set CONFIDENT_API_KEY, OPENAI_API_KEY, and OPENAI_MODEL."""

import asyncio
import os

from openai import AsyncOpenAI

from confident_trace import init


async def main():
    init()
    async with AsyncOpenAI() as client:
        stream = await client.responses.create(
            model=os.environ["OPENAI_MODEL"], input="Say hello!", stream=True
        )
        async with stream:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    print(event.delta, end="", flush=True)
        print()


if __name__ == "__main__":
    asyncio.run(main())
