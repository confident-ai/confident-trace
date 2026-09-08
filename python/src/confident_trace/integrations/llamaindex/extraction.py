"""Only documented data payloads; omit configuration, state and native LLM spans."""

from llama_index.core.base.llms.base import BaseLLM
from llama_index.core.schema import BaseNode, NodeWithScore, QueryBundle
from llama_index.core.tools.types import BaseTool, ToolOutput
from workflows.events import Event, StopEvent

from ..._semconv import genai_v1_37_0 as ai


def describe(id_, instance, *, include_llm=False):
    name = id_.rsplit("-", 5)[0][:256]  # Dispatch IDs end in a UUID, not a user name.
    method = name.rsplit(".", 1)[-1]
    if isinstance(instance, BaseLLM):
        if not include_llm or method not in {
            "chat",
            "achat",
            "complete",
            "acomplete",
            "stream_chat",
            "astream_chat",
            "stream_complete",
            "astream_complete",
        }:
            return None
        model = getattr(getattr(instance, "metadata", None), "model_name", "unknown")
        return name, {ai.GEN_AI_OPERATION_NAME: "chat", ai.GEN_AI_REQUEST_MODEL: model}
    if method in {"retrieve", "aretrieve"}:
        return name, {ai.GEN_AI_OPERATION_NAME: "retrieval"}
    attrs = {}
    if isinstance(instance, BaseTool):
        # __call__ delegates to call/acall; it is not a second tool execution.
        if method not in ("call", "acall"):
            return None
        name = instance.metadata.name or type(instance).__name__
        attrs = {ai.GEN_AI_OPERATION_NAME: "execute_tool", ai.GEN_AI_TOOL_NAME: name}
        name = f"execute_tool {name}"
    elif method == "run":
        from llama_index.core.agent.workflow import AgentWorkflow, BaseWorkflowAgent

        if isinstance(instance, (BaseWorkflowAgent, AgentWorkflow)):
            agent = getattr(instance, "name", None) or type(instance).__name__
            attrs = {
                ai.GEN_AI_OPERATION_NAME: "invoke_agent",
                ai.GEN_AI_AGENT_NAME: agent,
            }
            name = f"invoke_agent {agent}"
    return name, attrs


def data(value, depth=0):
    if depth > 6:
        return "[truncated]"
    if isinstance(value, QueryBundle):
        return value.query_str
    if isinstance(value, NodeWithScore):
        return {"text": data(value.node, depth + 1), "score": value.score}
    if isinstance(value, BaseNode):
        return getattr(value, "text", None)  # No document metadata or embeddings.
    if isinstance(value, ToolOutput):
        return value.content
    if isinstance(value, StopEvent):
        return data(value.result, depth + 1)
    if isinstance(value, Event):
        # Events may also contain model/configuration/state objects. Read only
        # explicit payload fields, never recursively dump a framework model.
        fields = (
            "input",
            "user_msg",
            "query",
            "query_str",
            "output",
            "response",
            "delta",
            "tool_name",
            "tool_kwargs",
            "tool_output",
        )
        return {
            key: data(getattr(value, key), depth + 1)
            for key in fields
            if hasattr(value, key)
        }
    if type(value) in (list, tuple):
        return [data(v, depth + 1) for v in value[:128]]
    if type(value) is dict:
        from itertools import islice

        return {
            k: data(v, depth + 1)
            for k, v in islice(value.items(), 128)
            if type(k) is str
        }
    if hasattr(value, "response") and type(value.response) is str:
        return value.response
    return value


def request(op, bound):
    if (getattr(op.span, "attributes", None) or {}).get(
        ai.GEN_AI_OPERATION_NAME
    ) == "chat":
        from ..._core.spans import content

        values = bound.arguments.get("messages")
        if values is None:
            values = [{"role": "user", "content": bound.arguments.get("prompt", "")}]
        normalized = _messages(values)
        content(op.span, ai.GEN_AI_INPUT_MESSAGES, normalized)
        op.input(normalized)
        return
    # Workflow runner args include serialized broker state and propagation tags.
    allowed = (
        "query",
        "query_str",
        "query_bundle",
        "str_or_query_bundle",
        "input",
        "user_msg",
        "ev",
        "event",
        "start_event",
    )
    if (getattr(op.span, "attributes", None) or {}).get(
        ai.GEN_AI_OPERATION_NAME
    ) == "execute_tool":
        allowed += ("args", "kwargs")
    op.input({k: data(v) for k, v in bound.arguments.items() if k in allowed})


def response(op, value):
    if (getattr(op.span, "attributes", None) or {}).get(
        ai.GEN_AI_OPERATION_NAME
    ) == "chat":
        from ..._core.spans import content

        message = getattr(value, "message", None)
        if message is None:
            message = {"role": "assistant", "content": getattr(value, "text", "")}
        normalized = _messages([message])
        content(op.span, ai.GEN_AI_OUTPUT_MESSAGES, normalized)
        op.output(normalized)
        raw = getattr(value, "raw", None)
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        for key, source in (
            (ai.GEN_AI_USAGE_INPUT_TOKENS, "prompt_tokens"),
            (ai.GEN_AI_USAGE_OUTPUT_TOKENS, "completion_tokens"),
        ):
            count = usage.get(source)
            if isinstance(count, (int, float)):
                op.span.set_attribute(key, count)
        return
    if isinstance(value, ToolOutput) and value.is_error:
        error = value.exception
        op.failure(type(error).__name__ if error else "ToolError")
    op.output(data(value))


def _messages(values):
    result = []
    for message in values:
        if isinstance(message, dict):
            role, text = message.get("role", "user"), message.get("content", "")
        else:
            role = getattr(message, "role", "user")
            role = getattr(role, "value", role)
            text = getattr(message, "content", "")
        if isinstance(text, str):
            result.append(
                {"role": str(role), "parts": [{"type": "text", "content": text}]}
            )
    return result
