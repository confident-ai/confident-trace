"""Install llama-index-core and llama-index-llms-openai; configure API keys."""

import asyncio
import os

from llama_index.core.agent.workflow import AgentStream, FunctionAgent
from llama_index.llms.openai import OpenAI

import confident_trace as ct


async def main():
    ct.init()
    try:
        agent = FunctionAgent(
            name="assistant",
            llm=OpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
        )
        with ct.span("request", thread_id="example-conversation"):
            handler = agent.run("Explain OpenTelemetry in one sentence.")
            try:
                async for event in handler.stream_events():
                    if isinstance(event, AgentStream):
                        print(event.delta, end="", flush=True)
                await handler  # Observe completion/errors as well as stream events.
                print()
            finally:
                if not handler.is_done():
                    await handler.cancel_run()
    finally:
        ct.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
