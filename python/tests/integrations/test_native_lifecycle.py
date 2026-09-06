import subprocess
import sys

import pytest


@pytest.mark.parametrize("imports_first", [False, True])
@pytest.mark.parametrize("disabled", [False, True])
def test_native_import_order_and_disabled(imports_first, disabled):
    pytest.importorskip("google.adk")
    pytest.importorskip("bedrock_agentcore")
    script = """
import os
import confident_trace as ct
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
if IMPORTS_FIRST:
    from google.adk.telemetry.tracing import tracer
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
if DISABLED_FLAG:
    os.environ['OTEL_SDK_DISABLED'] = 'true'
exporter = InMemorySpanExporter()
rt = ct.init(exporter=exporter)
if not IMPORTS_FIRST:
    from google.adk.telemetry.tracing import tracer
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
with tracer.start_as_current_span('native'):
    pass
ct.flush()
assert len(exporter.get_finished_spans()) == (0 if DISABLED_FLAG else 1)
if not DISABLED_FLAG:
    assert ct.init() is rt
    ct.shutdown()
    second = InMemorySpanExporter()
    ct.init(exporter=second)
    with tracer.start_as_current_span('after-reinit'):
        pass
    ct.flush()
    assert len(second.get_finished_spans()) == 1
    assert len(exporter.get_finished_spans()) == 1
ct.shutdown()
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            script.replace("IMPORTS_FIRST", repr(imports_first)).replace(
                "DISABLED_FLAG", repr(disabled)
            ),
        ],
        check=True,
        timeout=45,
    )
