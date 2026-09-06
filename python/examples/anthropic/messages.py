"""Set CONFIDENT_API_KEY, ANTHROPIC_API_KEY, and ANTHROPIC_MODEL."""

import os

from anthropic import Anthropic

from confident_trace import init

init()
client = Anthropic()
message = client.messages.create(
    model=os.environ["ANTHROPIC_MODEL"],
    max_tokens=128,
    messages=[{"role": "user", "content": "Say hello!"}],
)
for block in message.content:
    if block.type == "text":
        print(block.text)
