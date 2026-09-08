"""Explicit execution payloads only; never serialize agents, config or state."""

from ..._semconv import genai_v1_37_0 as ai


def text(value, fallback):
    return value[:256] if type(value) is str and value else fallback


def describe(kind, instance, params):
    attrs = {}
    if kind == "llm":
        model = text(getattr(instance, "model", None), "unknown")
        return f"chat {model}", {
            ai.GEN_AI_OPERATION_NAME: "chat",
            ai.GEN_AI_REQUEST_MODEL: model,
        }
    if kind == "agent":
        name = text(getattr(instance, "role", None), "agent")
        attrs = {
            ai.GEN_AI_OPERATION_NAME: ai.GEN_AI_OPERATION_NAME__INVOKE_AGENT,
            ai.GEN_AI_AGENT_NAME: name,
        }
        return f"invoke_agent {name}", attrs
    if kind == "tool":
        name = text(getattr(instance, "name", None), "tool")
        attrs = {
            ai.GEN_AI_OPERATION_NAME: ai.GEN_AI_OPERATION_NAME__EXECUTE_TOOL,
            ai.GEN_AI_TOOL_NAME: name,
        }
        return f"execute_tool {name}", attrs
    name = (
        params.get("method_name") if kind == "node" else getattr(instance, "name", None)
    )
    return f"{kind} {text(name, type(instance).__name__)}", attrs


def request(op, kind, instance, params, args, kwargs):
    if kind == "llm":
        from ..._core.spans import content

        value = params.get("messages", [])
        content(op.span, ai.GEN_AI_INPUT_MESSAGES, messages(value))
    elif kind == "tool":
        value = params.get("input", {"args": args, "kwargs": kwargs})
    elif kind == "task":
        value = {
            "description": getattr(instance, "description", None),
            "context": params.get("context"),
        }
    elif kind == "agent":
        value = params.get("inputs")
        task = params.get("task")
        if task is not None:
            value = {
                "description": getattr(task, "description", None),
                "context": params.get("context"),
            }
    elif kind == "node":
        value = {"args": params.get("args", ()), "kwargs": params.get("kwargs", {})}
    else:
        value = params.get("inputs")
    op.input(value)


def response(op, kind, value):
    if kind == "llm" and type(value) is str:
        from ..._core.spans import content

        content(
            op.span,
            ai.GEN_AI_OUTPUT_MESSAGES,
            [{"role": "assistant", "parts": [{"type": "text", "content": value}]}],
        )
    if kind in ("crew", "task", "agent"):
        value = getattr(value, "raw", value)
    elif kind == "node" and type(value) is tuple and len(value) == 2:
        value = value[0]  # Framework's second item is its event ID, not user output.
    op.output(value)


def tool_identity(params):
    call = params.get("tool_call")
    if call is None:
        return params.get("func_name"), params.get("call_id")
    if type(call) is dict:
        function = call.get("function")
        name = function.get("name") if type(function) is dict else call.get("name")
        return name, call.get("call_id") or call.get("id") or call.get("toolUseId")
    function = getattr(call, "function", None) or getattr(call, "function_call", None)
    name = getattr(function, "name", None) or getattr(call, "name", None)
    return name, getattr(call, "id", None)  # Never invent an unavailable call ID.


def messages(value):
    from .._shared.extraction import arguments, get, sequence, string

    if type(value) is str:
        value = [{"role": "user", "content": value}]
    result = []
    for message in sequence(value):
        role = string(get(message, "role")) or "user"
        text = get(message, "content")
        parts = [{"type": "text", "content": text}] if type(text) is str else []
        for block in sequence(text):
            if get(block, "type") == "text" and type(get(block, "text")) is str:
                parts.append({"type": "text", "content": get(block, "text")})
        for call in sequence(get(message, "tool_calls")):
            function = get(call, "function", {})
            part = {
                "type": "tool_call",
                "name": get(function, "name", ""),
                "arguments": arguments(get(function, "arguments", {})),
            }
            if type(get(call, "id")) is str:
                part["id"] = get(call, "id")
            parts.append(part)
        if role == "tool":
            parts = [{"type": "tool_call_response", "response": text}]
            if type(get(message, "tool_call_id")) is str:
                parts[0]["id"] = get(message, "tool_call_id")
        result.append({"role": role, "parts": parts})
    return result
