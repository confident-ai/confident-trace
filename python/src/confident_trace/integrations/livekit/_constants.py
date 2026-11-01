"""Framework-owned instrumentation identity."""

SCOPE_NAME = "livekit-agents"
# LiveKit calls the model SDK inside this retry-attempt span; its parent,
# llm_request, carries the GenAI operation.
INFERENCE_SPANS = frozenset({"llm_request_run"})
