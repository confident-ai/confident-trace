"""Only init() is needed to trace supported provider calls.

Set CONFIDENT_API_KEY and OPENAI_API_KEY before running.
"""

from openai import OpenAI

from confident_trace import init

init()
client = OpenAI()
response = client.responses.create(model="gpt-4.1-mini", input="Hello")
print(response.output_text)
