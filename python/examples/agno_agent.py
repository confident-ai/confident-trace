"""Install agno and openai; set OPENAI_API_KEY."""

import os

from agno.agent import Agent
from agno.models.openai import OpenAIChat

import confident_trace as ct


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
        agent = Agent(
            name="assistant",
            model=OpenAIChat(id=os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
            tools=[lookup],
        )
        with ct.span("request", thread_id="example-conversation"):
            print(agent.run("Use lookup on hello.").content)
    finally:
        ct.shutdown()


if __name__ == "__main__":
    main()
