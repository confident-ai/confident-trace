"""google_genai request/response and endpoint normalization."""

from urllib.parse import urlsplit

from ..._core import runtime as _runtime
from ..._core.safety import safe
from ..._core.spans import content
from ..._semconv import genai_v1_37_0 as ai
from .._shared.extraction import get, sequence, string


def parts(value, depth=0):
    if depth > 4:
        return [{"type": "unsupported", "content_omitted": True}]
    if type(value) is str:
        return [{"type": "text", "content": value}]
    result = []
    for block in sequence(value):
        text = get(block, "text")
        if type(text) is str:
            result.append({"type": "text", "content": text})
        elif get(block, "function_call") is not None:
            call = get(block, "function_call")
            result.append(
                {
                    "type": "tool_call",
                    "id": get(call, "id"),
                    "name": get(call, "name", ""),
                    "arguments": get(call, "args"),
                }
            )
        elif get(block, "function_response") is not None:
            call = get(block, "function_response")
            result.append(
                {
                    "type": "tool_call_response",
                    "id": get(call, "id"),
                    "response": get(call, "response"),
                }
            )
        elif (
            get(block, "inline_data") is not None or get(block, "file_data") is not None
        ):
            result.append({"type": "media", "content_omitted": True})
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
        body = get(message, "content", get(message, "parts", ""))
        role = get(message, "role", "user") or "user"
        p = parts(body)
        result.append({"role": "assistant" if role == "model" else role, "parts": p})
    return result


def google_messages(value):
    """Normalize Google's Content/Part/string unions without loading media."""
    result = []
    for item in sequence(value if type(value) in (list, tuple) else [value]):
        if get(item, "parts") is not None or get(item, "role") is not None:
            result.extend(messages([item]))
            continue
        item_parts = parts(item if type(item) in (str, list, tuple) else [item])
        role = "assistant" if get(item, "function_call") is not None else "user"
        if result and result[-1]["role"] == role:
            result[-1]["parts"].extend(item_parts)
        else:
            result.append({"role": role, "parts": item_parts})
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
    options = get(client, "_http_options", {})
    base = get(options, "base_url")
    if base is not None and type(base) is not str:
        if type(base).__module__ in ("httpx", "httpx._urls", "httpx2", "httpx2._urls"):
            base = str(base)
        else:
            base = None
    parsed = urlsplit(base) if base else None
    host = parsed.hostname if parsed else None
    name = "google_genai"
    vertex = get(client, "vertexai", get(client, "_vertexai"))
    name = ai.GEN_AI_PROVIDER_NAME__GCP_GEN_AI
    if host and host.endswith("aiplatform.googleapis.com") or vertex is True:
        name = ai.GEN_AI_PROVIDER_NAME__GCP_VERTEX_AI
    elif host == "generativelanguage.googleapis.com" or vertex is False:
        name = ai.GEN_AI_PROVIDER_NAME__GCP_GEMINI
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
    config = kwargs.get("config")
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
    conversation = kwargs.get("conversation")
    conversation = (
        conversation if type(conversation) is str else get(conversation, "id")
    )
    if type(conversation) is str:
        safe(op.span.set_attribute, ai.GEN_AI_CONVERSATION_ID, conversation)
        if op.is_entry:
            safe(op.span.set_attribute, "confident.trace.thread_id", conversation)
    rt = _runtime.current()
    if rt and rt.policy.enabled:
        value = kwargs.get("messages", kwargs.get("input", kwargs.get("contents", [])))
        normalized = google_messages(value)
        content(op.span, ai.GEN_AI_INPUT_MESSAGES, normalized)
        if op.is_entry:
            content(op.span, "confident.trace.input", normalized)
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
    conversation = get(value, "conversation")
    conversation = (
        conversation if type(conversation) is str else get(conversation, "id")
    )
    if type(conversation) is str:
        safe(op.span.set_attribute, ai.GEN_AI_CONVERSATION_ID, conversation)
        if op.is_entry:
            safe(op.span.set_attribute, "confident.trace.thread_id", conversation)
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
                if attr == ai.GEN_AI_USAGE_OUTPUT_TOKENS:
                    extras = ("thoughts_token_count",)
                number += sum(
                    (v for k in extras if type((v := get(usage, k))) is int and v >= 0)
                )
                safe(op.span.set_attribute, attr, number)
                break
    output, reasons = ([], [])
    for candidate in sequence(get(value, "candidates")):
        reason = finish_reason(get(candidate, "finish_reason"))
        if reason:
            reasons.append(reason)
        output.extend(
            (
                {**m, "finish_reason": reason}
                for m in messages([get(candidate, "content", {})])
            )
        )
    if reasons:
        op.finish_reasons = reasons
        safe(op.span.set_attribute, ai.GEN_AI_RESPONSE_FINISH_REASONS, reasons)
    if output:
        content(op.span, ai.GEN_AI_OUTPUT_MESSAGES, output)
        if op.is_entry:
            content(op.span, "confident.trace.output", output)
