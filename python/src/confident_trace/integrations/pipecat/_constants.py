"""Framework-owned instrumentation identity."""

from ..._semconv import native

# Service spans and the conversation and turn spans use separate tracers.
SCOPE_NAMES = ("pipecat", "pipecat.turn")
INFERENCE_SCOPE = "pipecat"
# Pipecat calls the model SDK inside this span and records the call on it.
INFERENCE_SPANS = frozenset({"llm"})
# Conversation content Pipecat writes on its own spans. capture_content=False
# drops these at export, and the redactor rewrites them.
CONTENT_ATTRIBUTES = frozenset(
    {
        "input",
        "output",
        "text",
        "transcript",
        "text_output",
        "context_messages",
        native.SYSTEM_INSTRUCTIONS,
        "instructions",
        "session.instructions",
        "session_properties",
        "tool.arguments",
        "tool.result",
        "function_calls",
    }
)
TEST_CASE_ID_BODY_KEY = "confident_test_case_id"
