"""Install 'confident-trace[openai-agents]' alongside openai-agents.

Set OPENAI_API_KEY and CONFIDENT_API_KEY (or standard OTLP exporter settings).
"""

from agents import Agent, Runner

from confident_trace import init, shutdown, span

init()
agent = Agent(name="assistant", model="gpt-4.1-mini")
try:
    with span("request", thread_id="example-conversation"):
        print(Runner.run_sync(agent, "Say hello in one sentence.").final_output)
finally:
    shutdown()
