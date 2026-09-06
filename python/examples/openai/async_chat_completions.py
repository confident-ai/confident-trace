"""Set CONFIDENT_API_KEY, OPENAI_API_KEY, and OPENAI_MODEL."""

import asyncio
import os

from openai import AsyncOpenAI

from confident_trace import init


async def main():
    init()
    async with AsyncOpenAI() as client:
        response = await client.chat.completions.create(
            model=os.environ["OPENAI_MODEL"],
            messages=[{"role": "user", "content": "Say hello!"}],
        )
        print(response.choices[0].message.content)


if __name__ == "__main__":
    asyncio.run(main())
