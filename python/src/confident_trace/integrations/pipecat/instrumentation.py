"""Native Pipecat telemetry interoperability."""

from importlib.metadata import version

from opentelemetry import trace

from ..._attributes import Integration
from ..._core.runtime import log
from .._shared.flush import bounded_flush
from .._shared.lifecycle import register_native_inference
from .._shared.patching import install_targets
from ._constants import (
    CONTENT_ATTRIBUTES,
    INFERENCE_SCOPE,
    INFERENCE_SPANS,
    SCOPE_NAMES,
)
from .recording import (
    attach_recorder,
    conversation_trace_uuid,
    recording_started,
    upload_recording,
)
from .test_case import link_test_case, remember_test_case


def instrument(runtime):
    version("pipecat-ai")

    # Pipecat creates every span on the global provider. A provider passed to
    # init() is installed there only if nothing else has been.
    if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        trace.set_tracer_provider(runtime.provider)

    # Tracing is opt-in per pipeline; selecting this integration turns it on.
    # An explicit enable_tracing=False is respected.
    def init_wrapper(original, method):
        def init(wrapped, instance, args, kwargs):
            if runtime.active:
                kwargs.setdefault("enable_tracing", True)
            return wrapped(*args, **kwargs)

        return init

    # _cleanup ends the conversation span. A bot process is often stopped as
    # soon as its pipeline finishes, before the batch processor's next export.
    # The trace id is read before _cleanup ends the conversation span, and the
    # recording is uploaded once the processors have delivered their audio.
    def cleanup_wrapper(original, method):
        async def cleanup(wrapped, instance, args, kwargs):
            trace_uuid = conversation_trace_uuid(instance)
            try:
                return await wrapped(*args, **kwargs)
            finally:
                await upload_recording(runtime, instance, trace_uuid)
                if getattr(instance, "_enable_tracing", False):
                    await bounded_flush(runtime, "Pipecat")

        return cleanup

    # Pipecat Cloud and the development runner pass the /start request body to
    # the bot on its runner arguments.
    def runner_arguments_wrapper(original, method):
        def post_init(wrapped, instance, args, kwargs):
            result = wrapped(*args, **kwargs)
            if runtime.active:
                try:
                    remember_test_case(instance)
                except Exception:
                    log.debug("Pipecat test case link unavailable")
            return result

        return post_init

    def conversation_wrapper(original, method):
        def start_conversation(wrapped, instance, args, kwargs):
            result = wrapped(*args, **kwargs)
            if runtime.active:
                try:
                    link_test_case(instance)
                except Exception:
                    log.debug("Pipecat test case link unavailable")
            return result

        return start_conversation

    def recorder_setup_wrapper(original, method):
        async def setup(wrapped, instance, args, kwargs):
            result = await wrapped(*args, **kwargs)
            if runtime.active and args:
                attach_recorder(runtime, instance, args[0])
            return result

        return setup

    def start_recording_wrapper(original, method):
        async def start_recording(wrapped, instance, args, kwargs):
            await recording_started(instance)
            return await wrapped(*args, **kwargs)

        return start_recording

    undo = install_targets(
        [("pipecat.pipeline.worker", "PipelineWorker", "__init__")], init_wrapper
    )
    if not undo:
        raise RuntimeError("Pipecat pipeline hook could not be installed")
    undo += install_targets(
        [("pipecat.pipeline.worker", "PipelineWorker", "_cleanup")], cleanup_wrapper
    )
    undo += install_targets(
        [("pipecat.runner.types", "RunnerArguments", "__post_init__")],
        runner_arguments_wrapper,
    )
    undo += install_targets(
        [
            (
                "pipecat.utils.tracing.turn_trace_observer",
                "TurnTraceObserver",
                "start_conversation_tracing",
            )
        ],
        conversation_wrapper,
    )
    undo += install_targets(
        [
            (
                "pipecat.processors.audio.audio_buffer_processor",
                "AudioBufferProcessor",
                "setup",
            )
        ],
        recorder_setup_wrapper,
    )
    undo += install_targets(
        [
            (
                "pipecat.processors.audio.audio_buffer_processor",
                "AudioBufferProcessor",
                "start_recording",
            )
        ],
        start_recording_wrapper,
    )
    for scope in SCOPE_NAMES:
        runtime.processor.integration_scopes[scope] = Integration.PIPECAT
        runtime.processor.content_scopes[scope] = CONTENT_ATTRIBUTES
    return undo + register_native_inference(INFERENCE_SCOPE, span_names=INFERENCE_SPANS)
