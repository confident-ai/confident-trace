"""Set CONFIDENT_API_KEY, GOOGLE_API_KEY, and GOOGLE_MODEL."""

import asyncio
import os

from google import genai

from confident_trace import init


async def main():
    init()
    async with genai.Client().aio as client:
        response = await client.models.generate_content(
            model=os.environ["GOOGLE_MODEL"], contents="Say hello!"
        )
        print(response.text)


if __name__ == "__main__":
    asyncio.run(main())
