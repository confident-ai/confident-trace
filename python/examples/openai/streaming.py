"""Responses streaming via create(); set the same variables as responses.py."""

import os

from openai import OpenAI

from confident_trace import init

init()
client = OpenAI()
with client.responses.create(
    model=os.environ["OPENAI_MODEL"], input="Say hello!", stream=True
) as stream:
    for event in stream:
        if event.type == "response.output_text.delta":
            print(event.delta, end="", flush=True)
print()
