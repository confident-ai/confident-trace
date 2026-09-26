"""Upload the call audio LiveKit records itself (`record=True`) when a job ends."""

import asyncio
import os
import threading
import urllib.request
import weakref
from urllib.parse import urlencode

from ..._core.runtime import log

MAX_RECORDING_BYTES = 50 * 1024 * 1024
UPLOAD_TIMEOUT_SECONDS = 5

_registered = weakref.WeakSet()


def register_recording_upload(runtime):
    from livekit.agents import get_job_context

    ctx = get_job_context(required=False)
    if ctx is None or ctx in _registered:
        return
    _registered.add(ctx)

    # Shutdown callbacks run after the recorder closes and before
    # _on_cleanup deletes the session directory that holds the file.
    async def upload_call_recording():
        await _upload(runtime, ctx)

    ctx.add_shutdown_callback(upload_call_recording)


def _read(path):
    if os.path.getsize(path) > MAX_RECORDING_BYTES:
        return None
    with open(path, "rb") as recording:
        return recording.read()


def _post(url, headers, body, result):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(
            request, timeout=UPLOAD_TIMEOUT_SECONDS
        ) as response:
            result.append(200 <= response.status < 300)
    except Exception:
        result.append(False)


async def _upload(runtime, ctx):
    target = runtime.recording_upload
    if not (runtime.active and target and runtime.policy.enabled):
        return
    try:
        report = ctx.make_session_report()
        path = report.audio_recording_path
        started_at = report.audio_recording_started_at
        room_sid = ctx.job.room.sid
        if path is None or started_at is None or not room_sid:
            return
        body = await asyncio.to_thread(_read, path)
        if not body:
            log.debug("LiveKit call recording is empty or too large to upload")
            return

        endpoint, headers = target
        query = urlencode({"threadId": room_sid, "startedAt": round(started_at * 1000)})
        result = []
        # A daemon thread so a hung upload cannot hold the worker past its bound.
        worker = threading.Thread(
            target=_post,
            args=(
                f"{endpoint}?{query}",
                {**headers, "content-type": "audio/ogg"},
                body,
                result,
            ),
            daemon=True,
        )
        worker.start()
        await asyncio.to_thread(worker.join, UPLOAD_TIMEOUT_SECONDS)
        if not result or not result[0]:
            log.debug("LiveKit call recording upload failed or timed out")
    except Exception:
        log.debug("LiveKit call recording upload failed")
