"""Run locally or inside AgentCore; uses normal AWS credentials and BEDROCK_MODEL.

Install: pip install -e './python[agentcore]' bedrock-agentcore boto3
Run: python python/examples/agentcore/bedrock.py
Invoke: POST /invocations with JSON {"prompt": "Hello"} on port 8080.
"""

import os

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from confident_trace import init

init(
    endpoint=os.getenv(
        "CONFIDENT_OTLP_ENDPOINT", "https://otel.confident-ai.com/v1/traces"
    ),
    protocol="http/protobuf",
    api_key=os.environ["CONFIDENT_API_KEY"],
    instrumentations=("agentcore", "bedrock"),
)
app = BedrockAgentCoreApp()
client = boto3.client("bedrock-runtime")


@app.entrypoint
async def invoke(payload, context):
    # Offloading preserves OTel context; cancellation does not stop a Boto3 call.
    import asyncio

    return await asyncio.to_thread(
        client.converse,
        modelId=os.environ["BEDROCK_MODEL"],
        messages=[{"role": "user", "content": [{"text": payload["prompt"]}]}],
    )


if __name__ == "__main__":
    app.run()
