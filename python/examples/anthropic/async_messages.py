"""Set CONFIDENT_API_KEY, ANTHROPIC_API_KEY, and ANTHROPIC_MODEL."""

import asyncio
import os

from anthropic import AsyncAnthropic

from confident_trace import init


async def main():
    init()
    async with AsyncAnthropic() as client:
        message = await client.messages.create(
            model=os.environ["ANTHROPIC_MODEL"],
            max_tokens=128,
            messages=[{"role": "user", "content": "Say hello!"}],
        )
        for block in message.content:
            if block.type == "text":
                print(block.text)


if __name__ == "__main__":
    asyncio.run(main())
