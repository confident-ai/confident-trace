"""Use synchronous Boto3 from asyncio by offloading the call to a worker thread.

Set CONFIDENT_API_KEY, AWS_DEFAULT_REGION, BEDROCK_MODEL_ID, and AWS credentials.
This is not native async Bedrock instrumentation. Cancelling the await does not
cancel an already-running Boto3 request.
"""

import asyncio
import os

import boto3

from confident_trace import init


def converse():
    client = boto3.client("bedrock-runtime")
    try:
        return client.converse(
            modelId=os.environ["BEDROCK_MODEL_ID"],
            messages=[{"role": "user", "content": [{"text": "Say hello!"}]}],
        )
    finally:
        client.close()


async def main():
    init()
    response = await asyncio.to_thread(converse)
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            print(block["text"])


if __name__ == "__main__":
    asyncio.run(main())
