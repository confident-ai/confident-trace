"""Native Strands spans inside AgentCore, using the shared OTel provider.

Install: pip install -e './python[agentcore]' bedrock-agentcore 'strands-agents[otel]'
Set CONFIDENT_API_KEY, BEDROCK_MODEL and normal AWS credentials.
Run this file; POST {"prompt": "Hello"} to /invocations on port 8080.
"""

import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool

from confident_trace import init

init(
    endpoint=os.getenv(
        "CONFIDENT_OTLP_ENDPOINT", "https://otel.confident-ai.com/v1/traces"
    ),
    protocol="http/protobuf",
    api_key=os.environ["CONFIDENT_API_KEY"],
)
app = BedrockAgentCoreApp()


@tool
def city_info(city: str) -> str:
    """Return an example fact about a city."""
    return f"Requested city: {city}"


@app.entrypoint
async def invoke(payload, context):
    agent = Agent(
        model=os.environ["BEDROCK_MODEL"],
        tools=[city_info],
        trace_attributes={"session.id": context.session_id}
        if context.session_id
        else {},
    )
    return str(await agent.invoke_async(payload["prompt"]))


if __name__ == "__main__":
    app.run()
