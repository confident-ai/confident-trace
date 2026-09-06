"""Set CONFIDENT_API_KEY, GOOGLE_API_KEY, and GOOGLE_MODEL."""

import os

from google import genai

from confident_trace import init

init()
client = genai.Client()
response = client.models.generate_content(
    model=os.environ["GOOGLE_MODEL"], contents="Say hello!"
)
print(response.text)
