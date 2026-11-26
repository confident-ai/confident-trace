"""Identifiers owned by Google ADK, not by Confident's emission schema."""

from typing import Final

SCOPE_NAME: Final = "gcp.vertex.agent"

# ADK's content routing knob, shared with other OTel GenAI instrumentations.
# ADK 2.14 defaults to its semconv-aligned schema, which leaves content off
# spans unless this names a span-bearing mode.
CAPTURE_MESSAGE_CONTENT: Final = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
SPAN_ONLY: Final = "SPAN_ONLY"
# ADK's legacy span-content knob; an explicit "false" is a content opt-out.
LEGACY_CONTENT_IN_SPANS: Final = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"
