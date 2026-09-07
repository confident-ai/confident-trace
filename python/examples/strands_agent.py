"""Install confident-trace and 'strands-agents[openai]'.

Set OPENAI_API_KEY and CONFIDENT_API_KEY (or standard OTLP exporter settings).
"""

import os

from strands import Agent
from strands.models.openai import OpenAIModel

from confident_trace import init, shutdown, span

init()
agent = Agent(
    model=OpenAIModel(model_id=os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
    callback_handler=None,
)
try:
    with span("request", thread_id="example-conversation"):
        print(agent("Say hello in one sentence."))
finally:
    shutdown()
