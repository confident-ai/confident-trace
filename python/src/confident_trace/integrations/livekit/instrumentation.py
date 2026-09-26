"""Native LiveKit Agents telemetry interoperability."""

import asyncio
import threading
from importlib.metadata import version

from opentelemetry import trace

from ..._attributes import Integration
from ..._core.runtime import log
from .._shared.lifecycle import register_native_inference
from .._shared.patching import install_targets
from ._constants import INFERENCE_SPANS, SCOPE_NAME
from .recording import register_recording_upload


def instrument(runtime):
    version("livekit-agents")
    from livekit.agents import JobContext, telemetry

    if not callable(getattr(JobContext, "_on_cleanup", None)):
        raise RuntimeError("LiveKit job cleanup hook is unavailable")

    # There is no public getter for LiveKit's provider. Preserve configured
    # settings; an unset one would get a private LiveKit Cloud provider.
    configured = getattr(telemetry.tracer, "_tracer_provider", None)
    if configured is runtime.provider or isinstance(
        configured, (trace.ProxyTracerProvider, trace.NoOpTracerProvider)
    ):
        telemetry.set_tracer_provider(runtime.provider)

    def cleanup_wrapper(original, method):
        async def cleanup(wrapped, instance, args, kwargs):
            try:
                return await wrapped(*args, **kwargs)
            finally:
                if runtime.active and runtime.processor:
                    # LiveKit ends the job root before _on_cleanup. Its ordinary
                    # shutdown callbacks run earlier and concurrently.
                    result = []

                    def flush():
                        try:
                            result.append(runtime.processor.force_flush(5000))
                        except Exception:
                            result.append(False)

                    # A broken exporter must not keep the worker alive through
                    # asyncio's executor shutdown; only the bounded join uses it.
                    try:
                        worker = threading.Thread(target=flush, daemon=True)
                        worker.start()
                        await asyncio.to_thread(worker.join, 5)
                        if not result or not result[0]:
                            log.debug("LiveKit final span flush failed or timed out")
                    except Exception:
                        log.debug("LiveKit final span flush failed or timed out")

        return cleanup

    def start_wrapper(original, method):
        async def start(wrapped, instance, args, kwargs):
            result = await wrapped(*args, **kwargs)
            if runtime.active:
                try:
                    register_recording_upload(runtime)
                except Exception:
                    log.debug("LiveKit call recording upload unavailable")
            return result

        return start

    undo = install_targets(
        [("livekit.agents", "JobContext", "_on_cleanup")], cleanup_wrapper
    )
    if not undo:
        raise RuntimeError("LiveKit job cleanup hook could not be installed")
    undo += install_targets(
        [("livekit.agents", "AgentSession", "start")], start_wrapper
    )
    runtime.processor.integration_scopes[SCOPE_NAME] = Integration.LIVEKIT
    return undo + register_native_inference(SCOPE_NAME, span_names=INFERENCE_SPANS)
