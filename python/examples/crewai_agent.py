"""Install confident-trace and crewai; configure OPENAI_API_KEY and OTLP export."""

from crewai import Agent, Crew, Task

import confident_trace as ct

ct.init()
agent = Agent(
    role="Explainer",
    goal="Give clear explanations",
    backstory="A patient teacher",
    llm="openai/gpt-4o-mini",
)
task = Task(
    description="Explain why the sky is blue in one sentence.",
    expected_output="One sentence",
    agent=agent,
)
try:
    with ct.span("request", thread_id="example-conversation"):
        print(Crew(agents=[agent], tasks=[task]).kickoff().raw)
finally:
    ct.shutdown()
