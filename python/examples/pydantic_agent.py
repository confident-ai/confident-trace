"""Install confident-trace and 'pydantic-ai-slim[openai]'.

Set OPENAI_API_KEY and CONFIDENT_API_KEY (or standard OTLP exporter settings).
"""

from pydantic_ai import Agent

from confident_trace import init, shutdown, span

init()
agent = Agent("openai:gpt-4.1-mini")
try:
    with span("request", thread_id="example-conversation"):
        print(agent.run_sync("Say hello in one sentence.").output)
finally:
    shutdown()
