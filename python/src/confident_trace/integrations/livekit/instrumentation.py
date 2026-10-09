"""Native LiveKit Agents telemetry interoperability."""

import asyncio
import threading
import weakref
from importlib.metadata import version

from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider

from ..._attributes import Integration
from ..._core.runtime import log
from .._shared.lifecycle import register_native_inference
from .._shared.patching import install_targets
from ._constants import INFERENCE_SPANS, SCOPE_NAME
from .recording import register_recording_upload


class _SharedProcessor(SpanProcessor):
    """Our processor on an application provider; its shutdown must not stop ours."""

    def __init__(self, delegate):
        self.delegate = delegate

    def on_start(self, span, parent_context=None):
        self.delegate.on_start(span, parent_context)

    def on_end(self, span):
        self.delegate.on_end(span)

    def force_flush(self, timeout_millis=30000):
        return self.delegate.force_flush(timeout_millis)

    def shutdown(self):
        pass


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

    shared = weakref.WeakSet()

    # A provider handed to LiveKit after init (e.g. per call) would otherwise
    # take its spans away from our exporter.
    def provider_wrapper(original, method):
        def set_provider(wrapped, instance, args, kwargs):
            result = wrapped(*args, **kwargs)
            provider = instance._tracer_provider
            if (
                runtime.active
                and runtime.processor
                and isinstance(provider, TracerProvider)
                and provider is not runtime.provider
                and provider not in shared
            ):
                try:
                    provider.add_span_processor(_SharedProcessor(runtime.processor))
                    shared.add(provider)
                except Exception:
                    log.debug("LiveKit tracer provider could not be shared")
            return result

        return set_provider

    undo = install_targets(
        [("livekit.agents", "JobContext", "_on_cleanup")], cleanup_wrapper
    )
    if not undo:
        raise RuntimeError("LiveKit job cleanup hook could not be installed")
    undo += install_targets(
        [("livekit.agents", "AgentSession", "start")], start_wrapper
    )
    undo += install_targets(
        [("livekit.agents.telemetry.traces", "_DynamicTracer", "set_provider")],
        provider_wrapper,
    )
    runtime.processor.integration_scopes[SCOPE_NAME] = Integration.LIVEKIT
    return undo + register_native_inference(SCOPE_NAME, span_names=INFERENCE_SPANS)
