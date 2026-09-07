"""Install confident-trace alongside claude-agent-sdk and authenticate Claude.

Set CONFIDENT_API_KEY or standard OTLP exporter settings. Native CLI traces
require a Claude Code version supporting beta enhanced telemetry.
"""

import asyncio

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from confident_trace import init, shutdown, span


async def main():
    init()
    try:
        with span("request", thread_id="example-conversation"):
            async for message in query(
                prompt="Say hello in one sentence.",
                options=ClaudeAgentOptions(max_turns=3),
            ):
                if isinstance(message, ResultMessage):
                    print(message.result)
    finally:
        shutdown()


if __name__ == "__main__":
    asyncio.run(main())
