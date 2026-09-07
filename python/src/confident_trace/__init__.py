"""Confident conventions on standard OpenTelemetry."""

from ._attributes import Integration
from ._bootstrap import init
from ._core.runtime import SEMCONV_VERSION, flush, shutdown
from ._core.runtime import VERSION as __version__
from ._core.spans import span, update_trace

__all__ = [
    "Integration",
    "SEMCONV_VERSION",
    "__version__",
    "flush",
    "init",
    "shutdown",
    "span",
    "update_trace",
]
