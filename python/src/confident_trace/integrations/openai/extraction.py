"""openai request/response and endpoint normalization."""

from urllib.parse import urlsplit

from ... import _attributes as confident
from ..._core.safety import safe
from ..._core.spans import content
from ..._semconv import genai_v1_37_0 as ai
from .._shared.extraction import arguments, get, sequence, string


def parts(value, depth=0):
    if depth > 4:
        return [{"type": "unsupported", "content_omitted": True}]
    if type(value) is str:
        return [{"type": "text", "content": value}]
    result = []
    for block in sequence(value):
        text = get(block, "text")
        kind = get(block, "type")
        if type(text) is str:
            result.append({"type": "text", "content": text})
        elif kind == "function_call":
            name = get(block, "name")
            result.append(
                {
                    "type": "tool_call",
                    "id": get(block, "call_id", get(block, "id")),
                    "name": name if type(name) is str else "",
                    "arguments": arguments(
                        get(block, "input", get(block, "arguments"))
                    ),
                }
            )
        elif kind == "function_call_output":
            result.append(
                {
                    "type": "tool_call_response",
                    "id": get(block, "tool_use_id", get(block, "call_id")),
                    "response": parts(get(block, "content"), depth + 1)
                    if type(get(block, "content")) is list
                    else get(block, "content", get(block, "output")),
                }
            )
        elif kind in (
            "image_url",
            "input_image",
            "input_audio",
            "audio",
            "file",
            "input_file",
        ):
            result.append({"type": kind, "content_omitted": True})
        else:
            result.append({"type": "unsupported", "content_omitted": True})
    return result


def messages(value):
    if type(value) is str:
        return [{"role": "user", "parts": parts(value)}]
    result = []
    for message in sequence(value if type(value) in (list, tuple) else [value]):
        if type(message) is str:
            result.append({"role": "user", "parts": parts(message)})
            continue
        kind = get(message, "type")
        if kind in ("function_call", "function_call_output"):
            result.append(
                {
                    "role": "assistant" if kind == "function_call" else "tool",
                    "parts": parts([message]),
                }
            )
            continue
        body = get(message, "content", get(message, "parts", ""))
        role = get(message, "role", "user") or "user"
        if role == "tool":
            p = [
                {
                    "type": "tool_call_response",
                    "id": get(message, "tool_call_id"),
                    "response": body,
                }
            ]
        else:
            p = parts(body)
        for tool in sequence(get(message, "tool_calls")):
            fn = get(tool, "function", {})
            p.append(
                {
                    "type": "tool_call",
                    "id": get(tool, "id"),
                    "name": get(fn, "name", ""),
                    "arguments": arguments(get(fn, "arguments")),
                }
            )
        result.append({"role": "assistant" if role == "model" else role, "parts": p})
    return result


_PARAMETERS = (
    (
        ai.GEN_AI_REQUEST_MAX_TOKENS,
        ("max_completion_tokens", "max_output_tokens", "max_tokens"),
        int,
    ),
    (ai.GEN_AI_REQUEST_TEMPERATURE, ("temperature",), float),
    (ai.GEN_AI_REQUEST_TOP_P, ("top_p",), float),
    (ai.GEN_AI_REQUEST_TOP_K, ("top_k",), float),
    (ai.GEN_AI_REQUEST_FREQUENCY_PENALTY, ("frequency_penalty",), float),
    (ai.GEN_AI_REQUEST_PRESENCE_PENALTY, ("presence_penalty",), float),
    (ai.GEN_AI_REQUEST_SEED, ("seed",), int),
)


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


def connection(instance):
    """Read SDK configuration only; never infer providers from a model name."""
    client = get(instance, "_client", get(instance, "_api_client"))
    base = get(client, "_base_url")
    if base is not None and type(base) is not str:
        if type(base).__module__ in ("httpx", "httpx._urls", "httpx2", "httpx2._urls"):
            base = str(base)
        else:
            base = None
    parsed = urlsplit(base) if base else None
    host = parsed.hostname if parsed else None
    name = ai.GEN_AI_PROVIDER_NAME__OPENAI
    if host and (
        host.endswith(".openai.azure.com") or host.endswith(".services.ai.azure.com")
    ):
        name = ai.GEN_AI_PROVIDER_NAME__AZURE_AI_OPENAI
    attrs = {ai.GEN_AI_PROVIDER_NAME: name}
    if host:
        attrs[ai.SERVER_ADDRESS] = host
        port = parsed.port or {"https": 443, "http": 80}.get(parsed.scheme)
        if port:
            attrs[ai.SERVER_PORT] = port
    return attrs


def request(op, kwargs):
    if not op.span.is_recording():
        return
    config = kwargs
    config = config or {}
    model = kwargs.get("model")
    if type(model) is str:
        safe(op.span.set_attribute, ai.GEN_AI_REQUEST_MODEL, model)
    for attr, keys, expected in _PARAMETERS:
        for key in keys:
            value = get(config, key)
            if type(value) is int or (expected is float and type(value) is float):
                safe(op.span.set_attribute, attr, value)
                break
    count = get(config, "n", get(config, "candidate_count"))
    if type(count) is int and count != 1:
        safe(op.span.set_attribute, ai.GEN_AI_REQUEST_CHOICE_COUNT, count)
    stop = get(config, "stop_sequences", get(config, "stop"))
    if type(stop) is str:
        stop = [stop]
    if type(stop) in (list, tuple) and all((type(v) is str for v in stop)):
        safe(op.span.set_attribute, ai.GEN_AI_REQUEST_STOP_SEQUENCES, stop[:128])
    fmt = get(config, "response_format", get(get(config, "text"), "format"))
    fmt = get(fmt, "type")
    mime = get(config, "response_mime_type")
    if fmt in ("json_object", "json_schema") or mime == "application/json":
        safe(op.span.set_attribute, ai.GEN_AI_OUTPUT_TYPE, ai.GEN_AI_OUTPUT_TYPE__JSON)
    elif fmt == "text" or mime == "text/plain":
        safe(op.span.set_attribute, ai.GEN_AI_OUTPUT_TYPE, ai.GEN_AI_OUTPUT_TYPE__TEXT)
    modalities = get(config, "modalities", get(config, "response_modalities"))
    if type(modalities) in (list, tuple) and len(modalities) == 1:
        modality = string(modalities[0])
        output_type = {"text": "text", "image": "image", "audio": "speech"}.get(
            (modality or "").lower()
        )
        if output_type:
            safe(op.span.set_attribute, ai.GEN_AI_OUTPUT_TYPE, output_type)
    tier = kwargs.get("service_tier")
    if type(tier) is str and tier != "auto":
        safe(op.span.set_attribute, ai.OPENAI_REQUEST_SERVICE_TIER, tier)
    conversation = kwargs.get("conversation")
    conversation = (
        conversation if type(conversation) is str else get(conversation, "id")
    )
    if type(conversation) is str:
        safe(op.span.set_attribute, ai.GEN_AI_CONVERSATION_ID, conversation)
        if op.is_entry:
            safe(op.span.set_attribute, confident.TRACE_THREAD_ID, conversation)
    rt = op.runtime
    if rt and rt.policy.enabled:
        value = kwargs.get("messages", kwargs.get("input", kwargs.get("contents", [])))
        normalized = messages(value)
        content(op.span, ai.GEN_AI_INPUT_MESSAGES, normalized)
        if op.is_entry:
            content(op.span, confident.TRACE_INPUT, normalized)
        system = kwargs.get(
            "system", kwargs.get("instructions", get(config, "system_instruction"))
        )
        if system is not None:
            content(
                op.span,
                ai.GEN_AI_SYSTEM_INSTRUCTIONS,
                parts(get(system, "parts", system)),
            )


def response(op, value):
    if op.ended or not op.span.is_recording():
        return
    for key, attr in (
        ("id", ai.GEN_AI_RESPONSE_ID),
        ("response_id", ai.GEN_AI_RESPONSE_ID),
        ("model", ai.GEN_AI_RESPONSE_MODEL),
        ("model_version", ai.GEN_AI_RESPONSE_MODEL),
    ):
        v = get(value, key)
        if type(v) is str:
            safe(op.span.set_attribute, attr, v)
    for key, attr in (
        ("service_tier", ai.OPENAI_RESPONSE_SERVICE_TIER),
        ("system_fingerprint", ai.OPENAI_RESPONSE_SYSTEM_FINGERPRINT),
    ):
        v = get(value, key)
        if type(v) is str:
            safe(op.span.set_attribute, attr, v)
    conversation = get(value, "conversation")
    conversation = (
        conversation if type(conversation) is str else get(conversation, "id")
    )
    if type(conversation) is str:
        safe(op.span.set_attribute, ai.GEN_AI_CONVERSATION_ID, conversation)
        if op.is_entry:
            safe(op.span.set_attribute, confident.TRACE_THREAD_ID, conversation)
    usage = get(value, "usage", get(value, "usage_metadata", {}))
    for keys, attr in (
        (
            ("input_tokens", "prompt_tokens", "prompt_token_count"),
            ai.GEN_AI_USAGE_INPUT_TOKENS,
        ),
        (
            ("output_tokens", "completion_tokens", "candidates_token_count"),
            ai.GEN_AI_USAGE_OUTPUT_TOKENS,
        ),
    ):
        for key in keys:
            number = get(usage, key)
            if type(number) is int and number >= 0:
                extras = ()
                number += sum(
                    (v for k in extras if type((v := get(usage, k))) is int and v >= 0)
                )
                safe(op.span.set_attribute, attr, number)
                break
    output, reasons = ([], [])
    for choice in sequence(get(value, "choices")):
        reason = finish_reason(get(choice, "finish_reason"))
        if reason:
            reasons.append(reason)
        message = get(choice, "message")
        if message is not None:
            output.extend(({**m, "finish_reason": reason} for m in messages([message])))
    raw = get(value, "output")
    if raw:
        status = get(value, "status")
        reason = (
            "stop"
            if status == "completed"
            else finish_reason(get(get(value, "incomplete_details"), "reason"))
        )
        if status == "failed":
            reason = "error"
        if reason:
            reasons.append(reason)
        output = [
            {
                "role": "assistant",
                "parts": [p for m in messages(raw) for p in m["parts"]],
                "finish_reason": reason,
            }
        ]
    if reasons:
        op.finish_reasons = reasons
        safe(op.span.set_attribute, ai.GEN_AI_RESPONSE_FINISH_REASONS, reasons)
    if output:
        content(op.span, ai.GEN_AI_OUTPUT_MESSAGES, output)
        if op.is_entry:
            content(op.span, confident.TRACE_OUTPUT, output)
