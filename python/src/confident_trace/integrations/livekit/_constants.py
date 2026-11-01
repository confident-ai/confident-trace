"""Framework-owned instrumentation identity."""

SCOPE_NAME = "livekit-agents"
# LiveKit calls the model SDK inside this retry-attempt span; its parent,
# llm_request, carries the GenAI operation.
INFERENCE_SPANS = frozenset({"llm_request_run"})
# Confident AI's voice simulations dispatch the agent with this key in the job
# metadata, naming the simulated test case the call's trace belongs to.
TEST_CASE_ID_METADATA_KEY = "confident_test_case_id"
