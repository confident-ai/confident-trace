"""Upload the call audio LiveKit records itself (`record=True`) when a job ends."""

import asyncio
import os
import ssl
import threading
import urllib.request
import weakref
from urllib.parse import urlencode

from opentelemetry import trace

from ..._core.runtime import log

# About what the upload bound carries at 50 Mbps, roughly 25 minutes of call.
MAX_RECORDING_BYTES = 18 * 1024 * 1024
# Leaves the final span flush its own share of LiveKit's 10s shutdown budget.
UPLOAD_TIMEOUT_SECONDS = 3
SPAN_BATCH_PATH = "/v1/traces"
CALL_RECORDING_PATH = "/v1/call-recordings"
# LiveKit's own PII opt-out (livekit.agents.telemetry.utils.allow_pii_from_env).
ALLOW_PII_ENV_VAR = "LIVEKIT_TELEMETRY_ALLOW_PII"
FALSY = ("0", "false", "no", "off")

_jobs_with_recording_upload = weakref.WeakSet()


def register_recording_upload(runtime):
    from livekit.agents import get_job_context

    ctx = get_job_context(required=False)
    if ctx is None or ctx in _jobs_with_recording_upload:
        return
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return
    trace_uuid = f"{span_context.trace_id:032x}"
    _jobs_with_recording_upload.add(ctx)

    # Shutdown callbacks run after the recorder closes and before
    # _on_cleanup deletes the session directory that holds the file.
    async def upload_call_recording():
        await _upload(runtime, ctx, trace_uuid)

    ctx.add_shutdown_callback(upload_call_recording)


def _recording_endpoint(runtime):
    if runtime.otlp_http_export is None:
        return None
    endpoint, headers = runtime.otlp_http_export
    if not endpoint.endswith(SPAN_BATCH_PATH):
        return None
    return endpoint[: -len(SPAN_BATCH_PATH)] + CALL_RECORDING_PATH, headers


def _pii_withheld():
    raw = os.environ.get(ALLOW_PII_ENV_VAR)
    return raw is not None and raw.strip().lower() in FALSY


def _read(path):
    if os.path.getsize(path) > MAX_RECORDING_BYTES:
        return None
    with open(path, "rb") as recording:
        return recording.read()


def _ssl_context(skip_verify):
    if not skip_verify:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _post(url, headers, body, result, skip_verify=False):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(
            request, timeout=UPLOAD_TIMEOUT_SECONDS, context=_ssl_context(skip_verify)
        ) as response:
            result.append(200 <= response.status < 300)
    except Exception:
        result.append(False)


async def _upload(runtime, ctx, trace_uuid):
    recording_endpoint = _recording_endpoint(runtime)
    if not (runtime.active and recording_endpoint and runtime.policy.enabled):
        return
    if _pii_withheld():
        return
    try:
        report = ctx.make_session_report()
        path = report.audio_recording_path
        started_at = report.audio_recording_started_at
        if path is None or started_at is None:
            return
        body = await asyncio.to_thread(_read, path)
        if not body:
            log.warning("LiveKit call recording is empty or too large to upload")
            return

        endpoint, headers = recording_endpoint
        query = urlencode(
            {"traceUuid": trace_uuid, "startedAt": round(started_at * 1000)}
        )
        result = []
        # A daemon thread so a hung upload cannot hold the worker past its bound.
        worker = threading.Thread(
            target=_post,
            args=(
                f"{endpoint}?{query}",
                {**headers, "content-type": "audio/ogg"},
                body,
                result,
                runtime.tls_skip_verify,
            ),
            daemon=True,
        )
        worker.start()
        await asyncio.to_thread(worker.join, UPLOAD_TIMEOUT_SECONDS)
        if not result or not result[0]:
            log.warning("LiveKit call recording upload failed or timed out")
    except Exception:
        log.debug("LiveKit call recording upload failed")
