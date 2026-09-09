"""Confident-owned extensions and identifiers, independent of OTel semconv versions."""

from enum import Enum
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

SPAN_INTEGRATION: Final = "confident.span.integration"


class Integration(str, Enum):
    """Canonical Cloud UI labels for SDK and framework integrations."""

    LANGCHAIN = "LangChain"
    CREWAI = "CrewAI"
    LLAMAINDEX = "LlamaIndex"
    OPENAI_AGENTS = "OpenAI Agents"
    OPENAI = "OpenAI"
    ANTHROPIC = "Anthropic"
    PYDANTIC_AI = "PydanticAI"
    GOOGLE_ADK = "Google ADK"
    OPENROUTER = "OpenRouter"
    STRANDS = "Strands"
    OPENTELEMETRY = "OpenTelemetry"
    OPENINFERENCE = "OpenInference"
    AGENTCORE = "AgentCore"
    # Integrations without an existing Cloud icon label.
    GOOGLE_GENAI = "Google GenAI"
    BEDROCK = "Bedrock"
    CLAUDE_AGENT_SDK = "Claude Agent SDK"
    MICROSOFT_AGENT_FRAMEWORK = "Microsoft Agent Framework"
    AGNO = "Agno"
    SMOLAGENTS = "Smolagents"


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

# Shared manual tracing extensions.
SPAN_TYPE = "confident.span.type"
PROJECT_CONTEXT_KEY = "confident_trace.project"
SUPPRESS_CONTEXT_KEY = "confident_trace.suppressed"
THREAD_ID = "confident.trace.thread.id"
THREAD_TAGS = "confident.trace.thread.tags"
THREAD_METADATA = "confident.trace.thread.metadata"
LLM_COST_PER_INPUT_TOKEN = "confident.llm.cost_per_input_token"
LLM_COST_PER_OUTPUT_TOKEN = "confident.llm.cost_per_output_token"
CONTENT_FIELDS = frozenset(
    (
        "input",
        "output",
        "metadata",
        "retrieval_context",
        "context",
        "expected_output",
        "tools_called",
        "expected_tools",
    )
)
SPAN_FIELDS = {key: f"confident.span.{key}" for key in CONTENT_FIELDS}
TRACE_FIELDS = MappingProxyType(
    {
        **TRACE_FIELDS,
        **{key: f"confident.trace.{key}" for key in CONTENT_FIELDS},
        "test_case_id": "confident.trace.test_case_id",
    }
)

TRACE_CONTEXT_KEY: Final = "confident_trace.trace_context"
DEFER_TRACE_CONTEXT_KEY: Final = "confident_trace.defer_trace_context"
