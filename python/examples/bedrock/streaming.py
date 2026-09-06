"""Set CONFIDENT_API_KEY, AWS_DEFAULT_REGION, BEDROCK_MODEL_ID, and AWS credentials."""

import os

import boto3

from confident_trace import init

init()
client = boto3.client("bedrock-runtime")
response = client.converse_stream(
    modelId=os.environ["BEDROCK_MODEL_ID"],
    messages=[{"role": "user", "content": [{"text": "Say hello!"}]}],
)
stream = response["stream"]
try:
    for event in stream:
        text = event.get("contentBlockDelta", {}).get("delta", {}).get("text")
        if text:
            print(text, end="", flush=True)
finally:
    stream.close()
    client.close()
print()
