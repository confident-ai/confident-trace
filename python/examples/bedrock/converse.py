"""Set CONFIDENT_API_KEY, AWS_DEFAULT_REGION, and BEDROCK_MODEL_ID.

Use normal Boto3 AWS credentials (profile, environment, or workload role).
"""

import os

import boto3

from confident_trace import init

init()
client = boto3.client("bedrock-runtime")
response = client.converse(
    modelId=os.environ["BEDROCK_MODEL_ID"],
    messages=[{"role": "user", "content": [{"text": "Say hello!"}]}],
)
for block in response["output"]["message"]["content"]:
    if "text" in block:
        print(block["text"])
client.close()
