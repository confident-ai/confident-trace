"""Run from python/ while livekit_agent.py is running, with OPENAI_API_KEY too:
venv/bin/python examples/livekit_caller.py
"""

import asyncio
from base64 import b64encode

from deepeval.test_case import Audio
from deepeval.voice.connectors.providers.livekit import LiveKitConnector
from dotenv import load_dotenv
from openai import OpenAI

from confident_trace import Media, init, shutdown, span, update_span, update_trace

LINES = (
    "Hi! Can you recommend a good book for a long flight?",
    "Something shorter, maybe under three hundred pages?",
    "Great, thanks. That's all I needed. Bye!",
)


def speak(text):
    speech = OpenAI().audio.speech.create(
        model="gpt-4o-mini-tts", voice="alloy", input=text, response_format="wav"
    )
    return Audio(
        dataBase64=b64encode(speech.content).decode("ascii"), mimeType="audio/wav"
    )


def message(role, content, audio):
    return {
        "role": role,
        "content": content,
        "audio": Media(encoded=audio.dataBase64, mime_type=audio.mimeType),
    }


def text_only(message):
    return {"role": message["role"], "content": message["content"]}


async def main():
    connector = LiveKitConnector(turn_detection="patient")
    await connector.connect()
    print(f"Joined room {connector.room_name}")
    messages = []
    try:
        for line in LINES:
            spoken = await asyncio.to_thread(speak, line)
            turn = await connector.exchange_turn(spoken)
            print(f"user:  {turn.provider_transcript or line}")
            print(f"agent: {turn.transcript or '<no transcript>'}")
            messages.append(message("user", line, spoken))
            messages.append(message("assistant", turn.transcript or "", turn.audio))
    finally:
        await connector.disconnect()
    return messages


if __name__ == "__main__":
    load_dotenv()
    init()
    conversation = asyncio.run(main())
    with span("caller-conversation", type="agent"):
        update_span(input=conversation[:-1], output=conversation[-1])
        update_trace(
            input=[text_only(m) for m in conversation[:-1]],
            output=text_only(conversation[-1]),
        )
    shutdown()
    print(f"Sent the caller's trace with audio on {len(conversation)} messages")
