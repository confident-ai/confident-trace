"""Install smolagents[openai]; configure OpenAI and Confident keys."""

import os

from smolagents import OpenAIModel, ToolCallingAgent
from smolagents.memory import FinalAnswerStep

import confident_trace as ct


def main():
    ct.init()
    try:
        agent = ToolCallingAgent(
            model=OpenAIModel(
                os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                api_key=os.environ["OPENAI_API_KEY"],
            ),
            tools=[],
            stream_outputs=True,
        )
        with ct.span("request", thread_id="example-conversation"):
            stream = agent.run("Explain OpenTelemetry in one sentence.", stream=True)
            try:
                for event in stream:
                    # smolagents renders model deltas; print the final typed result.
                    if isinstance(event, FinalAnswerStep):
                        print(event.output)
            finally:
                # Early close can raise upstream; do not hide that exception.
                stream.close()
    finally:
        ct.shutdown()


if __name__ == "__main__":
    main()
