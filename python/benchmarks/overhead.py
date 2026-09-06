"""Run from the repo root: PYTHONPATH=python/src python python/benchmarks/overhead.py."""

import json
import platform
import timeit

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

import confident_trace as ct
from confident_trace.integrations._shared.lifecycle import wrapper
from confident_trace.integrations.openai.instrumentation import begin, finish


class Discard(SpanExporter):
    def export(self, spans):
        return SpanExportResult.SUCCESS


@ct.span
def decorated():
    return 1


@ct.span
def stream():
    yield 1


def simulated_call(**kwargs):
    return (
        {"choices": [{"index": 0, "delta": {"content": "hello"}}]} for _ in range(20)
    )


instrumented_call = wrapper(begin, finish)


def provider_stream():
    for _ in instrumented_call(
        simulated_call, None, (), {"model": "benchmark", "messages": []}
    ):
        pass


results = {
    "python": platform.python_version(),
    "platform": platform.platform(),
    "sdk_version": ct.__version__,
    "genai_version": ct.SEMCONV_VERSION,
    "note": "Microseconds per invocation; stream measurements exclude network. 20 chunks per provider stream.",
}
for mode in ("disabled", "non_recording", "recording"):
    ct.shutdown()
    if mode != "disabled":
        kwargs = {"sampler": ALWAYS_OFF} if mode == "non_recording" else {}
        ct.init(
            tracer_provider=TracerProvider(shutdown_on_exit=False, **kwargs),
            exporter=Discard(),
            instrumentations=(),
        )
    results[mode + "_call_us"] = timeit.timeit(decorated, number=10000) * 100
    results[mode + "_stream_us"] = (
        timeit.timeit(lambda: list(stream()), number=10000) * 100
    )
    results[mode + "_provider_stream_us"] = (
        timeit.timeit(provider_stream, number=1000) * 1000
    )
ct.shutdown()
print(json.dumps(results, indent=2))
