"""Run from python/ with CONFIDENT_API_KEY in .env: venv/bin/python examples/manual_media.py"""

import pathlib

from dotenv import load_dotenv

from confident_trace import Media, init, shutdown, span, update_span

load_dotenv()
init()

recording = Media(uri=str(pathlib.Path(__file__).parent / "example.wav"))

with span("call-review"):
    update_span(
        input=[
            {
                "role": "user",
                "content": "Did the reviewer approve the submission?",
                "audio": recording,
            }
        ],
        output="The reviewer approved the submission on the call.",
    )

shutdown()
