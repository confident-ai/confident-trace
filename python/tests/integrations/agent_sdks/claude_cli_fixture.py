"""Offline CLI protocol fixture; it does not emulate native Claude telemetry."""

import json
import os
import sys
from pathlib import Path

capture = os.environ.get("CT_ENV_CAPTURE")
if capture:
    Path(capture).write_text(
        json.dumps(
            {
                key: value
                for key, value in os.environ.items()
                if key.startswith(
                    ("OTEL_", "CLAUDE_CODE_", "TRACEPARENT", "TRACESTATE")
                )
            }
        )
    )


def send(value):
    print(json.dumps(value), flush=True)


for line in sys.stdin:
    item = json.loads(line)
    if item["type"] == "control_request":
        send(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": item["request_id"],
                    "response": {},
                },
            }
        )
    elif item["type"] == "user":
        send(
            {
                "type": "assistant",
                "message": {
                    "id": "message-1",
                    "role": "assistant",
                    "model": "offline",
                    "content": [{"type": "text", "text": "hello"}],
                },
            }
        )
        send(
            {
                "type": "result",
                "subtype": "success",
                "duration_ms": 1,
                "duration_api_ms": 1,
                "is_error": False,
                "num_turns": 1,
                "session_id": "session-1",
                "total_cost_usd": 0,
                "usage": {"input_tokens": 2, "output_tokens": 1},
                "result": "hello",
            }
        )
