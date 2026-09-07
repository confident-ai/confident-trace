"""Normalize public callback payloads without serializing SDK configuration."""

from itertools import islice

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage

from ..._core.spans import content
from ..._semconv import genai_v1_37_0 as ai


def mapping(value):
    return value if type(value) is dict else {}


def name(serialized, options):
    return str(options.get("name") or mapping(serialized).get("name") or "Runnable")[
        :256
    ]


def messages(values, *, output=False):
    result = []
    for value in values[:128]:
        if isinstance(value, BaseMessage):
            data = vars(value)
            role = {"human": "user", "ai": "assistant"}.get(
                data.get("type"), data.get("type", "user")
            )
            text = data.get("content", "")
            parts = []
            if type(text) is str:
                parts.append({"type": "text", "content": text})
            elif type(text) is list:
                for block in text[:128]:
                    if type(block) is str:
                        parts.append({"type": "text", "content": block})
                    elif (
                        type(block) is dict
                        and block.get("type") == "text"
                        and type(block.get("text")) is str
                    ):
                        parts.append({"type": "text", "content": block["text"]})
            for call in data.get("tool_calls", [])[:128]:
                if type(call.get("name")) is str:
                    part = {
                        "type": "tool_call",
                        "name": call["name"],
                        "arguments": call.get("args", {}),
                    }
                    if type(call.get("id")) is str:
                        part["id"] = call["id"]
                    parts.append(part)
            if role == "tool":
                parts = [{"type": "tool_call_response", "response": text}]
                if type(data.get("tool_call_id")) is str:
                    parts[0]["id"] = data["tool_call_id"]
            message = {"role": role, "parts": parts}
            if output:
                reason = mapping(data.get("response_metadata")).get("finish_reason", "")
                message["finish_reason"] = reason if type(reason) is str else ""
            result.append(message)
        elif type(value) is str:
            message = {
                "role": "assistant" if output else "user",
                "parts": [{"type": "text", "content": value}],
            }
            if output:
                message["finish_reason"] = ""
            result.append(message)
    return result


def generic(value, depth=0):
    if depth > 6:
        return "[truncated]"
    if isinstance(value, BaseMessage):
        return messages([value])
    if isinstance(value, Document):
        return {"page_content": value.page_content}  # Metadata can contain credentials.
    if type(value) is dict:
        return {
            k: generic(v, depth + 1)
            for k, v in islice(value.items(), 128)
            if type(k) is str
        }
    if type(value) in (list, tuple):
        return [generic(v, depth + 1) for v in value[:128]]
    return value  # ContentPolicy rejects arbitrary objects without calling repr.


def request(op, kind, value, options):
    md = mapping(options.get("metadata"))
    params = mapping(options.get("invocation_params"))
    if kind in ("chat", "text_completion"):
        model = (
            md.get("ls_model_name") or params.get("model_name") or params.get("model")
        )
        provider = md.get("ls_provider")
        for key, item in (
            (ai.GEN_AI_REQUEST_MODEL, model),
            (ai.GEN_AI_PROVIDER_NAME, provider),
        ):
            if type(item) is str:
                op.span.set_attribute(key, item[:256])
        content(
            op.span,
            ai.GEN_AI_INPUT_MESSAGES,
            messages(value[0] if kind == "chat" and value else value),
        )
    else:
        op.input(generic(value))
    conversation = md.get("thread_id")
    if type(conversation) is str:
        op.span.set_attribute(ai.GEN_AI_CONVERSATION_ID, conversation[:4096])
    if kind == "execute_tool":
        call_id = options.get("tool_call_id")
        if type(call_id) is str:
            op.span.set_attribute(ai.GEN_AI_TOOL_CALL_ID, call_id)


def response(op, kind, value):
    if kind not in ("chat", "text_completion"):
        op.output(generic(value))
        return
    generations = getattr(value, "generations", None) or []
    output = []
    usage = None
    for group in generations[
        :1
    ]:  # One callback run per input; alternatives share usage.
        for generation in group[:128]:
            message = getattr(generation, "message", None)
            converted = messages(
                [message if message is not None else generation.text], output=True
            )
            info = mapping(getattr(generation, "generation_info", None))
            if converted and type(info.get("finish_reason")) is str:
                converted[0]["finish_reason"] = info["finish_reason"]
            output.extend(converted)
            if message is not None and usage is None:
                usage = mapping(vars(message).get("usage_metadata")) or None
                metadata = mapping(vars(message).get("response_metadata"))
                model = metadata.get("model_name") or metadata.get("model")
                if type(model) is str:
                    op.span.set_attribute(ai.GEN_AI_RESPONSE_MODEL, model)
    content(op.span, ai.GEN_AI_OUTPUT_MESSAGES, output)
    llm_output = mapping(getattr(value, "llm_output", None))
    usage = (
        usage
        or mapping(llm_output.get("token_usage"))
        or mapping(llm_output.get("usage"))
    )
    for key, choices in (
        (ai.GEN_AI_USAGE_INPUT_TOKENS, ("input_tokens", "prompt_tokens")),
        (ai.GEN_AI_USAGE_OUTPUT_TOKENS, ("output_tokens", "completion_tokens")),
    ):
        amount = next((usage[k] for k in choices if type(usage.get(k)) is int), None)
        if amount is not None and amount >= 0:
            op.span.set_attribute(key, amount)
    if output:
        op.span.set_attribute(
            ai.GEN_AI_RESPONSE_FINISH_REASONS, [m["finish_reason"] for m in output]
        )
