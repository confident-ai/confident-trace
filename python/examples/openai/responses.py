"""Set CONFIDENT_API_KEY, OPENAI_API_KEY, and OPENAI_MODEL."""

import os

from openai import OpenAI

from confident_trace import init

init()
client = OpenAI()
response = client.responses.create(model=os.environ["OPENAI_MODEL"], input="Say hello!")
print(response.output_text)
