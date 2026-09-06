"""Confident-owned extensions and identifiers, independent of OTel semconv versions."""

from types import MappingProxyType
from typing import Final

SCOPE_NAME: Final = "confident_trace"
ENTRY_CONTEXT_KEY: Final = "confident_trace.entry"
PROVIDER_CALL_CONTEXT_KEY: Final = "confident_trace.provider_call"

TRACE_NAME: Final = "confident.trace.name"
TRACE_INPUT: Final = "confident.trace.input"
TRACE_OUTPUT: Final = "confident.trace.output"
TRACE_TAGS: Final = "confident.trace.tags"
TRACE_METADATA: Final = "confident.trace.metadata"
TRACE_ENVIRONMENT: Final = "confident.trace.environment"
TRACE_USER_ID: Final = "confident.trace.user_id"
TRACE_THREAD_ID: Final = "confident.trace.thread_id"
TRACE_TURN_ID: Final = "confident.trace.turn_id"

SPAN_INPUT: Final = "confident.span.input"
SPAN_OUTPUT: Final = "confident.span.output"
SPAN_CONTENT_TRUNCATED: Final = "confident.span.content_truncated"

# Public update_trace keyword names map explicitly to owned attributes.
TRACE_FIELDS = MappingProxyType(
    {
        "name": TRACE_NAME,
        "input": TRACE_INPUT,
        "output": TRACE_OUTPUT,
        "tags": TRACE_TAGS,
        "metadata": TRACE_METADATA,
        "environment": TRACE_ENVIRONMENT,
        "user_id": TRACE_USER_ID,
        "thread_id": TRACE_THREAD_ID,
        "turn_id": TRACE_TURN_ID,
    }
)
