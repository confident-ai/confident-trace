"""Confident conventions on standard OpenTelemetry."""

from ._attributes import Integration
from ._bootstrap import init
from ._core.runtime import SEMCONV_VERSION, flush, shutdown
from ._core.runtime import VERSION as __version__
from ._core.scopes import project, suppress_tracing
from ._core.spans import (
    SpanType,
    span,
    turn,
    update_llm_span,
    update_span,
    update_trace,
)
from ._types import ThreadFields

__all__ = [
    "Integration",
    "ThreadFields",
    "SEMCONV_VERSION",
    "__version__",
    "flush",
    "init",
    "shutdown",
    "SpanType",
    "span",
    "turn",
    "update_span",
    "update_llm_span",
    "project",
    "suppress_tracing",
    "update_trace",
]
