"""Set CONFIDENT_API_KEY, ANTHROPIC_API_KEY, and ANTHROPIC_MODEL."""

import asyncio
import os

from anthropic import AsyncAnthropic

from confident_trace import init


async def main():
    init()
    async with AsyncAnthropic() as client:
        async with client.messages.stream(
            model=os.environ["ANTHROPIC_MODEL"],
            max_tokens=128,
            messages=[{"role": "user", "content": "Say hello!"}],
        ) as stream:
            async for text in stream.text_stream:
                print(text, end="", flush=True)
        print()


if __name__ == "__main__":
    asyncio.run(main())
