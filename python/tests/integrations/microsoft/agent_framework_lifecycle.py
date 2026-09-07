"""One process per lifecycle mode: framework globals never escape the scenario."""

import os
import sys
from unittest.mock import patch

from agent_framework import observability as obs
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct
from confident_trace.integrations._shared.lifecycle import native_inference_active


def main(mode):
    assert not obs.OBSERVABILITY_SETTINGS.enable_instrumentation
    if mode == "disabled":
        os.environ["OTEL_SDK_DISABLED"] = "true"
    elif mode == "sticky":
        obs.disable_instrumentation()
    elif mode in {"preexisting", "unrelated"}:
        obs.enable_instrumentation(enable_sensitive_data=True)

    provider = TracerProvider(shutdown_on_exit=False)
    trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    kwargs = (
        {"tracer_provider": TracerProvider(shutdown_on_exit=False)}
        if mode == "unrelated"
        else {}
    )
    try:
        with patch.object(
            obs, "enable_instrumentation", wraps=obs.enable_instrumentation
        ) as enable:
            rt = ct.init(
                exporter=exporter,
                instrumentations=("microsoft_agent_framework",),
                **kwargs,
            )
            if mode in {"disabled", "preexisting", "unrelated"}:
                enable.assert_not_called()
            else:
                enable.assert_called_once()

        if mode in {"disabled", "sticky"}:
            assert not obs.OBSERVABILITY_SETTINGS.enable_instrumentation
        else:
            assert obs.OBSERVABILITY_SETTINGS.enable_instrumentation
            assert obs.OBSERVABILITY_SETTINGS.enable_sensitive_data == (
                mode != "reinit"
            )
        if mode == "disabled":
            assert not rt.active
            assert exporter.get_finished_spans() == ()
            return

        with provider.get_tracer("agent_framework").start_as_current_span(
            "chat", attributes={"gen_ai.operation.name": "chat"}
        ):
            assert native_inference_active(rt) == (mode != "unrelated")
        ct.shutdown()
        # Check cleanup while a matching span is active, not against an empty context.
        with provider.get_tracer("agent_framework").start_as_current_span(
            "chat", attributes={"gen_ai.operation.name": "chat"}
        ):
            assert not native_inference_active(rt)
        if mode == "reinit":
            second = InMemorySpanExporter()
            with patch.object(
                obs, "enable_instrumentation", wraps=obs.enable_instrumentation
            ) as enable:
                ct.init(
                    exporter=second, instrumentations=("microsoft_agent_framework",)
                )
                enable.assert_not_called()
            with obs.get_tracer().start_as_current_span("after-reinit"):
                pass
            ct.flush()
            assert [s.name for s in second.get_finished_spans()] == ["after-reinit"]
            assert obs.OBSERVABILITY_SETTINGS.enable_instrumentation
    finally:
        ct.shutdown()
        provider.shutdown()
        if "tracer_provider" in kwargs:
            kwargs["tracer_provider"].shutdown()


if __name__ == "__main__":
    main(sys.argv[1])
