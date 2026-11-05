"""Normalize public callback payloads without serializing SDK configuration."""

import dataclasses
import datetime
import uuid
from enum import Enum
from itertools import islice

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage
from langchain_core.prompt_values import ChatPromptValue, StringPromptValue
from pydantic import BaseModel

from ..._core.spans import content
from ..._semconv import genai_v1_41_0 as ai
from .._shared.extraction import media

try:
    from langgraph.types import Command, Send
except ImportError:
    Command = Send = ()

_MEDIA_BLOCKS = {"image", "image_url", "file", "audio", "video"}
_COMMAND_FIELDS = ("goto", "update", "resume", "graph")
_TIMES = (datetime.datetime, datetime.date, datetime.time)


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
                    elif type(block) is not dict:
                        continue
                    elif block.get("type") == "text" and type(block.get("text")) is str:
                        parts.append({"type": "text", "content": block["text"]})
                    elif block.get("type") in _MEDIA_BLOCKS:
                        parts.append(media(block))
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


def fields(value):
    if isinstance(value, BaseModel):
        return vars(value)
    if not dataclasses.is_dataclass(value) or isinstance(value, type):
        return None
    data = getattr(value, "__dict__", None)
    names = [field.name for field in dataclasses.fields(value)]
    if type(data) is dict:
        return {name: data[name] for name in names if name in data}
    return {name: getattr(value, name) for name in names if hasattr(value, name)}


def generic(value, depth=0):
    if depth > 6:
        return "[truncated]"
    if type(value) in _TIMES:
        return value.isoformat()
    if type(value) is uuid.UUID:
        return str(value)
    if isinstance(value, Enum):
        return generic(value._value_, depth + 1)
    if type(value) in (set, frozenset):
        return [generic(v, depth + 1) for v in islice(value, 128)]
    if isinstance(value, ChatPromptValue):
        # Read known data fields, not arbitrary to_string/to_messages hooks.
        prompt_messages = vars(value).get("messages", [])
        if type(prompt_messages) in (list, tuple):
            return messages(prompt_messages)
        return value
    if isinstance(value, StringPromptValue):
        text = vars(value).get("text")
        return text if type(text) is str else value
    if isinstance(value, BaseMessage):
        return messages([value])
    if isinstance(value, Document):
        return {"page_content": value.page_content}  # Metadata can contain credentials.
    if isinstance(value, Command):
        result = {"type": "command"}
        for key in _COMMAND_FIELDS:
            item = getattr(value, key, None)
            if item is not None and not (
                type(item) in (list, tuple, dict) and not item
            ):
                result[key] = generic(item, depth + 1)
        return result
    if isinstance(value, Send):
        return {
            "type": "send",
            "node": value.node,
            "arg": generic(value.arg, depth + 1),
        }
    state = fields(value)
    if state is not None:
        return generic(dict(state), depth)
    if type(value) is dict:
        return {
            k: generic(v, depth + 1)
            for k, v in islice(value.items(), 128)
            if type(k) is str
        }
    if type(value) in (list, tuple):
        result = []
        for item in value[:128]:
            if isinstance(item, BaseMessage):
                result.extend(messages([item]))
            else:
                result.append(generic(item, depth + 1))
        return result
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


def interrupts(error):
    pending = error.args[0] if error.args else ()
    return {
        "__interrupt__": [
            generic(getattr(item, "value", item)) for item in tuple(pending)[:128]
        ]
    }


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
