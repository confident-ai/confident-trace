"""Run from python/ with CONFIDENT_API_KEY and LIVEKIT_* in .env, and leave it running:
venv/bin/python examples/livekit_agent.py dev
"""

import logging

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession, JobContext
from livekit.plugins import silero

from confident_trace import init

load_dotenv()
init()


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="You are a helpful voice AI assistant.")


server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: JobContext):
    session = AgentSession(
        stt="assemblyai/universal-streaming:en",
        llm="openai/gpt-4.1-mini",
        tts="cartesia/sonic-3",
        vad=silero.VAD.load(),
    )
    await session.start(
        agent=Assistant(),
        room=ctx.room,
        record={"audio": True, "traces": False, "logs": False},
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agents.cli.run_app(server)
