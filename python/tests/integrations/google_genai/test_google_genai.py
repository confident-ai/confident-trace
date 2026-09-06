import json

import httpx
import pytest
from conftest import enable, spans


def test_google_genai(telemetry):
    from google import genai
    from google.genai import types

    exporter = enable(telemetry, "google_genai")
    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "hello"}]}}],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
        "modelVersion": "gemini-test",
    }
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    with genai.Client(
        api_key="test",
        http_options=types.HttpOptions(client_args={"transport": transport}),
    ) as client:
        result = client.models.generate_content(model="gemini-test", contents="hi")
    assert result.text == "hello"
    assert spans(exporter)[0].attributes["gen_ai.usage.input_tokens"] == 2


@pytest.mark.asyncio
async def test_google_async_stream(telemetry):
    from google import genai
    from google.genai import types

    exporter = enable(telemetry, "google_genai")
    body = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": "hello"}]}}],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
    }
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            200,
            text="data: " + json.dumps(body) + "\n\n",
            headers={"content-type": "text/event-stream"},
        )
    )
    async with genai.Client(
        api_key="test",
        http_options=types.HttpOptions(async_client_args={"transport": transport}),
    ).aio as client:
        stream = await client.models.generate_content_stream(
            model="gemini-test", contents="hi"
        )
        assert [chunk.text async for chunk in stream] == ["hello"]
    assert len(spans(exporter)) == 1
