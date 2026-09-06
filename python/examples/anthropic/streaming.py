"""Set CONFIDENT_API_KEY, ANTHROPIC_API_KEY, and ANTHROPIC_MODEL."""

import os

from anthropic import Anthropic

from confident_trace import init

init()
client = Anthropic()
with client.messages.stream(
    model=os.environ["ANTHROPIC_MODEL"],
    max_tokens=128,
    messages=[{"role": "user", "content": "Say hello!"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
print()
