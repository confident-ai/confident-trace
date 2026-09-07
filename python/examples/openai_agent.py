"""Install 'confident-trace[openai-agents]' alongside openai-agents.

Set OPENAI_API_KEY and CONFIDENT_API_KEY (or standard OTLP exporter settings).
"""

import os

from agents import Agent, Runner

from confident_trace import init, shutdown, span

init()
agent = Agent(name="assistant", model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
try:
    with span("request", thread_id="example-conversation"):
        print(Runner.run_sync(agent, "Say hello in one sentence.").final_output)
finally:
    shutdown()
