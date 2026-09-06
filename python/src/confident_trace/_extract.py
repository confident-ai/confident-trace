"""Provider data extraction; contains no span lifecycle or transport logic."""

from __future__ import annotations

import json
from enum import Enum
from itertools import islice
from urllib.parse import urlsplit

from . import _genai as ai
from . import _runtime
from ._spans import content, safe


def get(value, key, default=None):
    if type(value) is dict:
        return value.get(key, default)
    # SDK models store data in __dict__; avoid invoking arbitrary properties.
    try:
        data = object.__getattribute__(value, "__dict__")
        return data.get(key, default) if type(data) is dict else default
    except (AttributeError, TypeError):
        return default


def sequence(value, limit=128):
    return islice(value if type(value) in (list, tuple) else (), limit)


def string(value):
    # Google SDK finish reasons are str enums, not plain strings.
    if isinstance(value, Enum):
        value = value.value
    return value if type(value) is str else None


def arguments(value):
    if type(value) is str and len(value) <= 16384:
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            pass  # Partial streaming JSON stays a string.
    return value


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
        elif kind in ("tool_use", "function_call"):
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
        elif kind in ("tool_result", "function_call_output"):
            result.append(
                {
                    "type": "tool_call_response",
                    "id": get(block, "tool_use_id", get(block, "call_id")),
                    "response": (
                        parts(get(block, "content"), depth + 1)
                        if type(get(block, "content")) is list
                        else get(block, "content", get(block, "output"))
                    ),
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
        elif kind in (
            "image",
            "image_url",
            "input_image",
            "input_audio",
            "audio",
            "document",
            "file",
            "input_file",
        ):
            result.append({"type": kind, "content_omitted": True})
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


def connection(provider, instance):
    """Read SDK configuration only; never infer providers from a model name."""
    client = get(instance, "_client", get(instance, "_api_client"))
    base = get(client, "_base_url")
    if provider == "google_genai":
        options = get(client, "_http_options", {})
        base = get(options, "base_url")
    # HTTPX URL objects are SDK-owned configuration objects.
    if base is not None and type(base) is not str:
        if type(base).__module__ in ("httpx", "httpx._urls", "httpx2", "httpx2._urls"):
            base = str(base)
        else:
            base = None
    parsed = urlsplit(base) if base else None
    host = parsed.hostname if parsed else None
    name = provider
    if provider == "google_genai":
        vertex = get(client, "vertexai", get(client, "_vertexai"))
        name = ai.GEN_AI_PROVIDER_NAME__GCP_GEN_AI
        if host and host.endswith("aiplatform.googleapis.com") or vertex is True:
            name = ai.GEN_AI_PROVIDER_NAME__GCP_VERTEX_AI
        elif host == "generativelanguage.googleapis.com" or vertex is False:
            name = ai.GEN_AI_PROVIDER_NAME__GCP_GEMINI
    elif (
        provider == "openai"
        and host
        and (
            host.endswith(".openai.azure.com")
            or host.endswith(".services.ai.azure.com")
        )
    ):
        name = ai.GEN_AI_PROVIDER_NAME__AZURE_AI_OPENAI
    attrs = {ai.GEN_AI_PROVIDER_NAME: name}
    if host:
        attrs[ai.SERVER_ADDRESS] = host
        port = parsed.port or {"https": 443, "http": 80}.get(parsed.scheme)
        if port:
            attrs[ai.SERVER_PORT] = port
    return attrs


# Explicit aliases reflect request shapes across supported SDK versions.
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


def request(op, provider, kwargs):
    if not op.span.is_recording():
        return
    config = kwargs.get("config") if provider == "google_genai" else kwargs
    config = config or {}
    model = kwargs.get("model")
    if type(model) is str:
        safe(op.span.set_attribute, ai.GEN_AI_REQUEST_MODEL, model)
    for attr, keys, expected in _PARAMETERS:
        for key in keys:
            value = get(config, key)
            if type(value) is int or expected is float and type(value) is float:
                safe(op.span.set_attribute, attr, value)
                break
    count = get(config, "n", get(config, "candidate_count"))
    if type(count) is int and count != 1:
        safe(op.span.set_attribute, ai.GEN_AI_REQUEST_CHOICE_COUNT, count)
    stop = get(config, "stop_sequences", get(config, "stop"))
    if type(stop) is str:
        stop = [stop]
    if type(stop) in (list, tuple) and all(type(v) is str for v in stop):
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
    if provider == "openai" and type(tier) is str and tier != "auto":
        safe(op.span.set_attribute, ai.OPENAI_REQUEST_SERVICE_TIER, tier)
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
        normalized = (
            google_messages(value) if provider == "google_genai" else messages(value)
        )
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


def response(op, value, provider):
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
    if provider == "openai":
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
                # Anthropic reports cache input separately; Gemini reports thoughts separately.
                extras = ()
                if provider == "anthropic" and attr == ai.GEN_AI_USAGE_INPUT_TOKENS:
                    extras = ("cache_creation_input_tokens", "cache_read_input_tokens")
                elif (
                    provider == "google_genai" and attr == ai.GEN_AI_USAGE_OUTPUT_TOKENS
                ):
                    extras = ("thoughts_token_count",)
                number += sum(
                    v for k in extras if type(v := get(usage, k)) is int and v >= 0
                )
                safe(op.span.set_attribute, attr, number)
                break
    output, reasons = [], []
    if provider == "openai":
        for choice in sequence(get(value, "choices")):
            reason = finish_reason(get(choice, "finish_reason"))
            if reason:
                reasons.append(reason)
            message = get(choice, "message")
            if message is not None:
                output.extend(
                    {**m, "finish_reason": reason} for m in messages([message])
                )
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
            # Responses output items comprise one candidate, not separate choices.
            output = [
                {
                    "role": "assistant",
                    "parts": [p for m in messages(raw) for p in m["parts"]],
                    "finish_reason": reason,
                }
            ]
    elif provider == "anthropic":
        reason = finish_reason(
            get(value, "stop_reason", get(get(value, "delta"), "stop_reason"))
        )
        if reason:
            reasons.append(reason)
        raw = get(value, "content")
        if raw is not None:
            output = [
                {"role": "assistant", "parts": parts(raw), "finish_reason": reason}
            ]
    else:
        for candidate in sequence(get(value, "candidates")):
            reason = finish_reason(get(candidate, "finish_reason"))
            if reason:
                reasons.append(reason)
            output.extend(
                {**m, "finish_reason": reason}
                for m in messages([get(candidate, "content", {})])
            )
    if reasons:
        op.finish_reasons = reasons
        safe(op.span.set_attribute, ai.GEN_AI_RESPONSE_FINISH_REASONS, reasons)
    if output:
        content(op.span, ai.GEN_AI_OUTPUT_MESSAGES, output)
        if op.is_entry:
            content(op.span, "confident.trace.output", output)


class Accumulator:
    """Bounded per-candidate state; usage and completion continue after the cap."""

    def __init__(self, provider):
        self.provider = provider
        self.candidates = {}
        self.remaining = None
        self.truncated = False

    def append(self, record, field, value):
        if type(value) is not str:
            return
        size = max(0, self.remaining)
        fragment = value[:size]
        self.remaining -= len(fragment)
        self.truncated |= len(value) > size
        record[field] = record.get(field, "") + fragment

    def candidate(self, index=0):
        if type(index) is not int or index < 0 or index >= 128:
            return None
        return self.candidates.setdefault(
            index, {"text": "", "tools": {}, "finish_reason": ""}
        )

    def tool(self, candidate, index):
        if (
            type(index) not in (int, str)
            or len(candidate["tools"]) >= 32
            and index not in candidate["tools"]
        ):
            self.truncated = True
            return None
        if type(index) is str:
            index = index[:256]
        return candidate["tools"].setdefault(
            index, {"type": "tool_call", "id": "", "name": "", "arguments": ""}
        )

    def __call__(self, op, chunk):
        if op.ended or not op.span.is_recording():
            return
        value = get(chunk, "response", get(chunk, "message", chunk))
        safe(response, op, value, self.provider)
        rt = _runtime.current()
        if not rt or not rt.policy.enabled:
            return
        if self.remaining is None:
            self.remaining = rt.policy.max_bytes // 4
        # Complete snapshots are authoritative (Responses/Anthropic final helpers).
        if get(value, "output") or get(value, "content"):
            return
        first = self.candidate()
        delta = get(chunk, "delta", {})
        if type(chunk) is str:
            self.append(first, "text", chunk)
        elif type(delta) is str:
            if get(chunk, "type") == "response.function_call_arguments.delta":
                tool = self.tool(first, get(chunk, "output_index", 0))
                if tool is not None:
                    self.append(tool, "arguments", delta)
            elif get(chunk, "type") == "response.output_text.delta":
                self.append(first, "text", delta)
        else:
            self.append(first, "text", get(delta, "text"))
        reason = finish_reason(get(delta, "stop_reason"))
        if reason:
            first["finish_reason"] = reason
        for choice in sequence(get(chunk, "choices")):
            candidate = self.candidate(get(choice, "index", 0))
            if candidate is None:
                continue
            reason = finish_reason(get(choice, "finish_reason"))
            if reason:
                candidate["finish_reason"] = reason
            change = get(choice, "delta", {})
            self.append(candidate, "text", get(change, "content"))
            for item in sequence(get(change, "tool_calls"), 32):
                tool = self.tool(candidate, get(item, "index", 0))
                if tool is None:
                    continue
                fn = get(item, "function", {})
                for field, value in (
                    ("id", get(item, "id")),
                    ("name", get(fn, "name")),
                    ("arguments", get(fn, "arguments")),
                ):
                    self.append(tool, field, value)
        block = get(chunk, "content_block", get(chunk, "item"))
        if get(block, "type") == "text":
            self.append(first, "text", get(block, "text"))
        if get(block, "type") in ("tool_use", "function_call"):
            tool = self.tool(first, get(chunk, "index", get(chunk, "output_index", 0)))
            if tool is not None:
                # 'done' snapshots are not additional fragments.
                if not tool["name"]:
                    self.append(tool, "name", get(block, "name"))
                    self.append(tool, "id", get(block, "call_id", get(block, "id")))
        fragment = get(delta, "partial_json")
        if type(fragment) is str:
            tool = self.tool(first, get(chunk, "index", 0))
            if tool is not None:
                self.append(tool, "arguments", fragment)
        for item in sequence(get(chunk, "candidates")):
            candidate = self.candidate(get(item, "index", 0) or 0)
            if candidate is None:
                continue
            reason = finish_reason(get(item, "finish_reason"))
            if reason:
                candidate["finish_reason"] = reason
            for part in sequence(get(get(item, "content", {}), "parts")):
                self.append(candidate, "text", get(part, "text"))
                fn = get(part, "function_call")
                if fn:
                    tool = self.tool(candidate, get(fn, "id") or get(fn, "name", ""))
                    if tool is not None and not tool["name"]:
                        self.append(tool, "name", get(fn, "name"))
                        self.append(tool, "id", get(fn, "id"))
                        encoded = rt.policy.encode(get(fn, "args"))
                        self.append(tool, "arguments", encoded)
        output = []
        for _, candidate in sorted(self.candidates.items()):
            blocks = (
                [{"type": "text", "content": candidate["text"]}]
                if candidate["text"]
                else []
            )
            blocks += [
                {**tool, "arguments": arguments(tool["arguments"])}
                for tool in candidate["tools"].values()
            ]
            if blocks:
                output.append(
                    {
                        "role": "assistant",
                        "parts": blocks,
                        "finish_reason": candidate["finish_reason"],
                    }
                )
        reasons = [
            candidate["finish_reason"]
            for _, candidate in sorted(self.candidates.items())
            if candidate["finish_reason"]
        ]
        if reasons:
            safe(op.span.set_attribute, ai.GEN_AI_RESPONSE_FINISH_REASONS, reasons)
        if output:
            content(op.span, ai.GEN_AI_OUTPUT_MESSAGES, output)
            if op.is_entry:
                content(op.span, "confident.trace.output", output)
        if self.truncated:
            safe(op.span.set_attribute, "confident.span.content_truncated", True)
