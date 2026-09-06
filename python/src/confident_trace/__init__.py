"""Confident conventions on standard OpenTelemetry."""

from ._runtime import SEMCONV_VERSION, flush, init, shutdown
from ._runtime import VERSION as __version__
from ._spans import span, update_trace

__all__ = [
    "SEMCONV_VERSION",
    "__version__",
    "flush",
    "init",
    "shutdown",
    "span",
    "update_trace",
]
