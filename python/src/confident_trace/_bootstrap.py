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
    project_exporter_factory=None,
    resource_attributes=None,
    capture_content=True,
    max_content_bytes=16384,
    redact=None,
    instrumentations=(
        "llamaindex",
        "agno",
        "smolagents",
        "crewai",
        "langchain",
        "langgraph",
        "openai_agents",
        "claude_agent_sdk",
        "pydantic_ai",
        "strands",
        "microsoft_agent_framework",
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
        project_exporter_factory=project_exporter_factory,
        resource_attributes=resource_attributes,
        capture_content=capture_content,
        max_content_bytes=max_content_bytes,
        redact=redact,
        _instrument=lambda rt: registry.instrument(rt, instrumentations),
    )
