"""Install smolagents[openai]; set OPENAI_API_KEY."""

import os

from smolagents import OpenAIModel, ToolCallingAgent, tool

import confident_trace as ct


@tool
def lookup(value: str) -> str:
    """Look up a value.

    Args:
        value: The value to look up.
    """
    with ct.span("lookup-storage"):
        return value.upper()


def main():
    ct.init()
    try:
        agent = ToolCallingAgent(
            model=OpenAIModel(
                os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                api_key=os.environ["OPENAI_API_KEY"],
            ),
            tools=[lookup],
        )
        with ct.span("request", thread_id="example-conversation"):
            print(agent.run("Use lookup on hello."))
    finally:
        ct.shutdown()


if __name__ == "__main__":
    main()
