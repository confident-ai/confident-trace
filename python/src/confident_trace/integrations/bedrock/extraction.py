"""Bedrock Converse payload extraction, independent of Botocore lifecycle."""

from ... import _attributes as confident
from ..._core import runtime as _runtime
from ..._core.safety import safe
from ..._core.spans import content
from ..._semconv import genai_v1_37_0 as ai
from .._shared.extraction import get, sequence, string


def parts(blocks, depth=0):
    if depth > 4:
        return [{"type": "unsupported", "content_omitted": True}]
    output = []
    for block in sequence(blocks):
        text = get(block, "text")
        tool = get(block, "toolUse")
        result = get(block, "toolResult")
        if type(text) is str:
            output.append({"type": "text", "content": text})
        elif type(tool) is dict:
            output.append(
                {
                    "type": "tool_call",
                    "id": get(tool, "toolUseId"),
                    "name": get(tool, "name", ""),
                    "arguments": get(tool, "input"),
                }
            )
        elif type(result) is dict:
            output.append(
                {
                    "type": "tool_call_response",
                    "id": get(result, "toolUseId"),
                    "response": parts(get(result, "content"), depth + 1),
                }
            )
        elif type(block) is dict and "json" in block:
            output.append({"type": "json", "content": block["json"]})
        else:
            # Binary data, documents, reasoning signatures, and unknown payloads
            # stay out of content capture; do not stringify SDK objects.
            output.append({"type": "unsupported", "content_omitted": True})
    return output


def messages(values):
    return [
        {"role": get(message, "role", "user"), "parts": parts(get(message, "content"))}
        for message in sequence(values)
    ]


def request(op, params):
    if not op.span.is_recording():
        return
    config = get(params, "inferenceConfig", {})
    for key, attr, types in (
        ("maxTokens", ai.GEN_AI_REQUEST_MAX_TOKENS, (int,)),
        ("temperature", ai.GEN_AI_REQUEST_TEMPERATURE, (int, float)),
        ("topP", ai.GEN_AI_REQUEST_TOP_P, (int, float)),
    ):
        value = get(config, key)
        if type(value) in types:
            safe(op.span.set_attribute, attr, value)
    stops = get(config, "stopSequences")
    if type(stops) is list and all(type(v) is str for v in stops):
        safe(op.span.set_attribute, ai.GEN_AI_REQUEST_STOP_SEQUENCES, stops[:128])
    guardrail = get(get(params, "guardrailConfig"), "guardrailIdentifier")
    if type(guardrail) is str:
        safe(op.span.set_attribute, ai.AWS_BEDROCK_GUARDRAIL_ID, guardrail)
    rt = _runtime.current()
    if not rt or not rt.policy.enabled:
        return
    value = messages(get(params, "messages", []))
    content(op.span, ai.GEN_AI_INPUT_MESSAGES, value)
    if op.is_entry:
        content(op.span, confident.TRACE_INPUT, value)
    system = get(params, "system")
    if system is not None:
        content(op.span, ai.GEN_AI_SYSTEM_INSTRUCTIONS, parts(system))


def usage(op, value):
    for key, attr in (
        ("inputTokens", ai.GEN_AI_USAGE_INPUT_TOKENS),
        ("outputTokens", ai.GEN_AI_USAGE_OUTPUT_TOKENS),
    ):
        number = get(value, key)
        if type(number) is int and number >= 0:
            if key == "inputTokens":
                # Bedrock reports uncached input separately from cache reads/writes.
                number += sum(
                    cached
                    for field in ("cacheReadInputTokens", "cacheWriteInputTokens")
                    if type(cached := get(value, field)) is int and cached >= 0
                )
            safe(op.span.set_attribute, attr, number)


def response(op, value):
    if not op.span.is_recording():
        return
    request_id = get(get(value, "ResponseMetadata"), "RequestId")
    if type(request_id) is str:
        safe(op.span.set_attribute, ai.GEN_AI_RESPONSE_ID, request_id)
    usage(op, get(value, "usage"))
    reason = finish_reason(get(value, "stopReason"))
    if reason:
        safe(op.span.set_attribute, ai.GEN_AI_RESPONSE_FINISH_REASONS, [reason])
    rt = _runtime.current()
    if not rt or not rt.policy.enabled:
        return
    message = get(get(value, "output"), "message")
    if message is not None:
        output = [{**item, "finish_reason": reason} for item in messages([message])]
        content(op.span, ai.GEN_AI_OUTPUT_MESSAGES, output)
        if op.is_entry:
            content(op.span, confident.TRACE_OUTPUT, output)


def finish_reason(value):
    reason = string(value)
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "max_tokens": "length",
        "tool_use": "tool_call",
        "tool_calls": "tool_call",
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
    }.get(reason, reason or "")
