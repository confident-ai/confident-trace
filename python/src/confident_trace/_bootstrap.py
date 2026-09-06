"""Public initialization connects the core runtime to optional integrations."""

from __future__ import annotations

from collections.abc import Mapping

from ._core import runtime
from .integrations import registry


def init(
    *,
    api_key=None,
    endpoint=None,
    protocol=None,
    headers: Mapping[str, str] | None = None,
    timeout=None,
    compression=None,
    tracer_provider=None,
    exporter=None,
    resource_attributes=None,
    capture_content=True,
    max_content_bytes=16384,
    redact=None,
    instrumentations=(
        "openai",
        "anthropic",
        "google_genai",
        "bedrock",
        "google_adk",
        "agentcore",
    ),
):
    """Initialize tracing; explicit values override environment configuration."""
    return runtime.init(
        api_key=api_key,
        endpoint=endpoint,
        protocol=protocol,
        headers=headers,
        timeout=timeout,
        compression=compression,
        tracer_provider=tracer_provider,
        exporter=exporter,
        resource_attributes=resource_attributes,
        capture_content=capture_content,
        max_content_bytes=max_content_bytes,
        redact=redact,
        _install=lambda rt: registry.install(rt, instrumentations),
    )
