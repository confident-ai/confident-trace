"""Only init() is needed to trace supported provider calls.

Set CONFIDENT_API_KEY and OPENAI_API_KEY before running.
"""

import os

from openai import OpenAI

from confident_trace import init

init()
client = OpenAI()
response = client.responses.create(
    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), input="Hello"
)
print(response.output_text)
