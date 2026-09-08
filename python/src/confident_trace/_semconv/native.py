"""Read-only recognition vocabulary for native telemetry, not an emission schema.

These names are observed on native ADK spans (tested with ADK 2.8.0). Their
presence does not establish a span's schema version. Unknown names/values pass
through unchanged; add aliases only when demonstrated by a compatibility test.
Do not import the active generated emission vocabulary here: changing our emitted
contract must not silently change which external telemetry we recognize.
"""

from typing import Final

OPERATION_NAME: Final = "gen_ai.operation.name"
INFERENCE_OPERATIONS: Final = frozenset({"generate_content", "chat"})

# OpenInference bridge spans use their own vocabulary, preserved on export.
OPENINFERENCE_KIND = "openinference.span.kind"
OPENINFERENCE_LLM = "LLM"


# Native scopes recognized by the private synchronous embedding attachment.
NATIVE_INTEGRATION_SCOPES = {
    "pydantic_ai": "pydantic-ai",
    "strands": "strands.telemetry.tracer",
    "google_adk": "gcp.vertex.agent",
}
