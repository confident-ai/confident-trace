"""Set CONFIDENT_API_KEY, GOOGLE_API_KEY, and GOOGLE_MODEL."""

import os

from google import genai

from confident_trace import init

init()
client = genai.Client()
for chunk in client.models.generate_content_stream(
    model=os.environ["GOOGLE_MODEL"], contents="Say hello!"
):
    if chunk.text:
        print(chunk.text, end="", flush=True)
print()
