"""Set CONFIDENT_API_KEY, GOOGLE_API_KEY, and GOOGLE_MODEL."""

import asyncio
import os

from google import genai

from confident_trace import init


async def main():
    init()
    async with genai.Client().aio as client:
        stream = await client.models.generate_content_stream(
            model=os.environ["GOOGLE_MODEL"], contents="Say hello!"
        )
        async for chunk in stream:
            if chunk.text:
                print(chunk.text, end="", flush=True)
        print()


if __name__ == "__main__":
    asyncio.run(main())
