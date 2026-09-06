"""Set CONFIDENT_API_KEY, OPENAI_API_KEY, and OPENAI_MODEL."""

import os

from openai import OpenAI

from confident_trace import init

init()
client = OpenAI()
response = client.chat.completions.create(
    model=os.environ["OPENAI_MODEL"],
    messages=[{"role": "user", "content": "Say hello!"}],
)
print(response.choices[0].message.content)
